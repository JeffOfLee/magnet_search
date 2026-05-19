from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

from magnet_search.download import DownloadError, DownloadResult, _snapshot_files, _changed_files


def _torrent_info_hash(data: bytes) -> str:
    info_start = data.find(b"4:infod")
    if info_start == -1:
        return ""
    depth = 0
    pos = info_start + 6
    while pos < len(data):
        if data[pos : pos + 1] == b"e":
            depth -= 1
            if depth < 0:
                return hashlib.sha1(data[info_start + 6 : pos + 1]).hexdigest()
            pos += 1
            continue
        if data[pos : pos + 1] == b"d" or data[pos : pos + 1] == b"l":
            depth += 1
            pos += 1
            continue
        if data[pos : pos + 1] == b"i":
            end = data.index(b"e", pos)
            pos = end + 1
            continue
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        pos = colon + 1 + length
    return ""


class QbittorrentDownloader:
    def __init__(
        self,
        url: str = "http://localhost:8080",
        username: str = "admin",
        password: str = "",
        poll_interval: float = 5.0,
        http_timeout: float = 30.0,
    ):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.poll_interval = poll_interval
        self.http_timeout = http_timeout
        self._session: httpx.Client | None = None

    def _client(self) -> httpx.Client:
        if self._session is not None:
            return self._session
        self._session = httpx.Client(timeout=self.http_timeout, trust_env=False)
        self._login()
        return self._session

    def _login(self) -> None:
        response = self._session.post(
            f"{self.url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
        )
        if response.status_code not in (200, 204) or (response.status_code == 200 and response.text.strip() != "Ok."):
            raise DownloadError(f"qBittorrent login failed: {response.text.strip()}")

    _retry_statuses = frozenset((502, 503, 504))

    def _api_get(self, endpoint: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(5):
            response = self._client().get(f"{self.url}{endpoint}", params=params)
            if response.status_code == 403:
                self._login()
                response = self._client().get(f"{self.url}{endpoint}", params=params)
            if response.status_code not in self._retry_statuses:
                response.raise_for_status()
                return response
            time.sleep(2 ** attempt)
        response.raise_for_status()
        return response

    def _api_post(self, endpoint: str, data: dict | None = None, files: dict | None = None) -> httpx.Response:
        for attempt in range(5):
            response = self._client().post(f"{self.url}{endpoint}", data=data, files=files)
            if response.status_code == 403:
                self._login()
                response = self._client().post(f"{self.url}{endpoint}", data=data, files=files)
            if response.status_code not in self._retry_statuses:
                response.raise_for_status()
                return response
            time.sleep(2 ** attempt)
        response.raise_for_status()
        return response

    def download(self, source: str, output_dir: Path) -> DownloadResult:
        source = source.strip()
        if not source:
            raise DownloadError("download source must be non-empty")

        output_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot_files(output_dir)

        existing_hashes = self._get_hashes()

        already_exists = False
        source_path = Path(source)
        if source_path.suffix.lower() == ".torrent" and source_path.exists():
            torrent_data = source_path.read_bytes()
            files_payload = {"torrents": (source_path.name, torrent_data, "application/x-bittorrent")}
            try:
                response = self._api_post(
                    "/api/v2/torrents/add",
                    data={"savepath": str(output_dir)},
                    files=files_payload,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    already_exists = True
                else:
                    raise
        else:
            try:
                response = self._api_post(
                    "/api/v2/torrents/add",
                    data={"urls": source, "savepath": str(output_dir)},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    already_exists = True
                else:
                    raise

        if not already_exists:
            if response.text.strip() and response.text.strip() != "Ok.":
                try:
                    result = json.loads(response.text)
                    if result.get("success_count", 0) == 0:
                        raise DownloadError(f"qBittorrent failed to add torrent: {response.text.strip()}")
                except json.JSONDecodeError:
                    raise DownloadError(f"qBittorrent failed to add torrent: {response.text.strip()}")

        if already_exists:
            if source_path.suffix.lower() == ".torrent" and source_path.exists():
                info_hash = _torrent_info_hash(source_path.read_bytes())
            else:
                info_hash = self._find_new_hash(self._get_hashes())
            if not info_hash:
                raise DownloadError("qBittorrent could not find already-existing torrent hash")
        else:
            info_hash = self._find_new_hash(existing_hashes)
            if info_hash is None:
                raise DownloadError("qBittorrent could not find added torrent")

        self._wait_for_completion(info_hash)

        after = _snapshot_files(output_dir)

        try:
            self._api_post("/api/v2/torrents/delete", data={"hashes": info_hash, "deleteFiles": "false"})
        except Exception:
            pass

        return DownloadResult(magnet=source, files=_changed_files(before, after))

    def _get_hashes(self) -> set[str]:
        try:
            response = self._api_get("/api/v2/torrents/info")
            return {t["hash"] for t in response.json()}
        except Exception:
            return set()

    def _find_new_hash(self, existing_hashes: set[str]) -> str | None:
        for _ in range(10):
            time.sleep(1)
            try:
                response = self._api_get("/api/v2/torrents/info")
                current = {t["hash"]: t for t in response.json()}
                for h, t in current.items():
                    if h not in existing_hashes and t.get("state") not in ("unknown",):
                        return h
            except Exception:
                time.sleep(1)
        return None

    def _wait_for_completion(self, info_hash: str) -> None:
        completed_states = frozenset(("pausedUP", "uploading", "stalledUP", "queuedUP", "forcedUP", "checkingUP"))
        while True:
            try:
                response = self._api_get("/api/v2/torrents/info", params={"hashes": info_hash})
                torrents = response.json()
                if not torrents:
                    raise DownloadError("qBittorrent torrent disappeared")

                torrent = torrents[0]
                state = torrent.get("state", "")
                progress = torrent.get("progress", 0)

                if state in ("error", "missingFiles"):
                    raise DownloadError(f"qBittorrent torrent error state: {state}")
                if state in completed_states and progress >= 1.0:
                    return
            except DownloadError:
                raise
            except Exception:
                pass
            time.sleep(self.poll_interval)
