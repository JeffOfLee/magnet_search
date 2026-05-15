from __future__ import annotations

from hashlib import sha1
from typing import Any
from urllib.parse import quote


class BencodeError(ValueError):
    pass


def _parse_value(data: bytes, index: int) -> tuple[Any, int]:
    if index >= len(data):
        raise BencodeError(f"unexpected end of data at byte {index}")
    marker = data[index:index + 1]
    if marker == b"i":
        end = data.find(b"e", index)
        if end == -1:
            raise BencodeError(f"unterminated integer at byte {index}")
        try:
            return int(data[index + 1:end]), end + 1
        except ValueError as exc:
            raise BencodeError(f"invalid integer at byte {index}") from exc
    if marker == b"l":
        values = []
        start = index
        index += 1
        while True:
            if index >= len(data):
                raise BencodeError(f"unterminated list at byte {start}")
            if data[index:index + 1] == b"e":
                return values, index + 1
            value, index = _parse_value(data, index)
            values.append(value)
    if marker == b"d":
        values = {}
        start = index
        index += 1
        while True:
            if index >= len(data):
                raise BencodeError(f"unterminated dictionary at byte {start}")
            if data[index:index + 1] == b"e":
                return values, index + 1
            key, index = _parse_value(data, index)
            value, index = _parse_value(data, index)
            values[key] = value
    if marker.isdigit():
        colon = data.find(b":", index)
        if colon == -1:
            raise BencodeError(f"missing byte string length terminator at byte {index}")
        try:
            length = int(data[index:colon])
        except ValueError as exc:
            raise BencodeError(f"invalid byte string length at byte {index}") from exc
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError(f"byte string at byte {index} extends beyond end of data")
        return data[start:end], end
    raise BencodeError(f"invalid bencode marker at byte {index}")


def _value_span(data: bytes, index: int) -> tuple[int, int]:
    if index >= len(data):
        raise BencodeError(f"unexpected end of data at byte {index}")
    start = index
    marker = data[index:index + 1]
    if marker == b"i":
        end = data.find(b"e", index)
        if end == -1:
            raise BencodeError(f"unterminated integer at byte {index}")
        try:
            int(data[index + 1:end])
        except ValueError as exc:
            raise BencodeError(f"invalid integer at byte {index}") from exc
        return start, end + 1
    if marker in {b"l", b"d"}:
        index += 1
        kind = "list" if marker == b"l" else "dictionary"
        while True:
            if index >= len(data):
                raise BencodeError(f"unterminated {kind} at byte {start}")
            if data[index:index + 1] == b"e":
                return start, index + 1
            _, index = _value_span(data, index)
    if marker.isdigit():
        colon = data.find(b":", index)
        if colon == -1:
            raise BencodeError(f"missing byte string length terminator at byte {index}")
        try:
            length = int(data[index:colon])
        except ValueError as exc:
            raise BencodeError(f"invalid byte string length at byte {index}") from exc
        end = colon + 1 + length
        if end > len(data):
            raise BencodeError(f"byte string at byte {index} extends beyond end of data")
        return start, end
    raise BencodeError(f"invalid bencode marker at byte {index}")


def extract_info_hash(torrent_bytes: bytes) -> str:
    if torrent_bytes[:1] != b"d":
        raise BencodeError("torrent root must be a dictionary")
    index = 1
    while True:
        if index >= len(torrent_bytes):
            raise BencodeError("unterminated dictionary at byte 0")
        if torrent_bytes[index:index + 1] == b"e":
            break
        key, index = _parse_value(torrent_bytes, index)
        if key == b"info" and torrent_bytes[index:index + 1] != b"d":
            raise BencodeError("info value must be a dictionary")
        value_start, value_end = _value_span(torrent_bytes, index)
        if key == b"info":
            return sha1(torrent_bytes[value_start:value_end]).hexdigest()
        index = value_end
    raise BencodeError("torrent does not contain an info dictionary")


def extract_trackers(torrent_bytes: bytes) -> list[str]:
    root, _ = _parse_value(torrent_bytes, 0)
    trackers: list[str] = []
    announce = root.get(b"announce") if isinstance(root, dict) else None
    if isinstance(announce, bytes):
        trackers.append(announce.decode("utf-8", errors="replace"))
    announce_list = root.get(b"announce-list") if isinstance(root, dict) else None
    if isinstance(announce_list, list):
        for group in announce_list:
            if isinstance(group, list):
                for value in group:
                    if isinstance(value, bytes):
                        trackers.append(value.decode("utf-8", errors="replace"))
    return list(dict.fromkeys(trackers))


def build_magnet(info_hash: str, display_name: str = "", trackers: list[str] | None = None) -> str:
    params: list[str] = [f"xt=urn:btih:{info_hash}"]
    if display_name:
        params.append(f"dn={quote(display_name, safe='')}")
    for tracker in trackers or []:
        params.append(f"tr={quote(tracker, safe='')}")
    return "magnet:?" + "&".join(params)
