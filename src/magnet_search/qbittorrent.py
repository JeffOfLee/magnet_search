from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from magnet_search.download import DownloadError, DownloadResult, _snapshot_files, _changed_files


@dataclass(frozen=True)
class QbittorrentDownloadStatus:
    name: str
    state: str
    progress: float
    size: int
    downloaded: int
    download_speed: int
    upload_speed: int
    eta: int
    seeds: int
    peers: int
    save_path: str


def _torrent_info_hash(data: bytes) -> str:
    info_start = data.find(b"4:infod")
    if info_start == -1:
        return ""
    info_value_start = info_start + 6
    pos = info_value_start + 1
    depth = 1
    while pos < len(data):
        if data[pos : pos + 1] == b"e":
            depth -= 1
            if depth == 0:
                return hashlib.sha1(data[info_value_start : pos + 1]).hexdigest()
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
        no_seed_checks: int = 3,
        verbose: bool = False,
    ):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.poll_interval = poll_interval
        self.http_timeout = http_timeout
        self.no_seed_checks = no_seed_checks
        self.verbose = verbose
        self._session: httpx.Client | None = None

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[qbittorrent] {msg}", flush=True)

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

        source_path = Path(source)
        short_name = source_path.name if source_path.suffix.lower() == ".torrent" else source
        self._log(f"download start: {short_name}")

        output_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot_files(output_dir)

        existing_hashes = self._get_hashes()

        already_exists = False
        if source_path.suffix.lower() == ".torrent" and source_path.exists():
            self._log(f"adding torrent file: {source_path.name} ({source_path.stat().st_size / 1024:.0f} KB)")
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
                    self._log("torrent already exists in qBittorrent (409)")
                    already_exists = True
                else:
                    raise
        else:
            self._log(f"adding magnet/url: {source[:80]}")
            try:
                response = self._api_post(
                    "/api/v2/torrents/add",
                    data={"urls": source, "savepath": str(output_dir)},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    self._log("torrent already exists in qBittorrent (409)")
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
            self._log(f"found existing hash: {info_hash}")
        else:
            info_hash = self._find_new_hash(existing_hashes)
            if info_hash is None:
                raise DownloadError("qBittorrent could not find added torrent")
            self._log(f"found new hash: {info_hash}")

        self._wait_for_completion(info_hash)

        after = _snapshot_files(output_dir)
        new_files = _changed_files(before, after)
        self._log(f"download done: {len(new_files)} file(s)")

        self._remove_torrent(info_hash)

        return DownloadResult(magnet=source, files=new_files)

    def _remove_torrent(self, info_hash: str) -> None:
        self._log(f"removing torrent: {info_hash}")
        try:
            self._api_post("/api/v2/torrents/delete", data={"hashes": info_hash, "deleteFiles": "false"})
        except Exception:
            pass

    def _get_hashes(self) -> set[str]:
        try:
            response = self._api_get("/api/v2/torrents/info")
            return {t["hash"] for t in response.json()}
        except Exception:
            return set()

    def active_download_sources(self) -> set[str]:
        active_states = frozenset(("downloading", "stalledDL", "forcedDL", "queuedDL", "metaDL", "checkingDL"))
        try:
            response = self._api_get("/api/v2/torrents/info")
            active: set[str] = set()
            for torrent in response.json():
                if torrent.get("state") not in active_states:
                    continue
                for key in ("magnet_uri", "hash", "name"):
                    value = torrent.get(key)
                    if value:
                        active.add(str(value))
            return active
        except Exception:
            return set()

    def list_downloads(self) -> list[QbittorrentDownloadStatus]:
        response = self._api_get("/api/v2/torrents/info")
        return [self._download_status_from_torrent(torrent) for torrent in response.json()]

    def _download_status_from_torrent(self, torrent: dict) -> QbittorrentDownloadStatus:
        return QbittorrentDownloadStatus(
            name=str(torrent.get("name", "")),
            state=str(torrent.get("state", "")),
            progress=_float_value(torrent.get("progress")),
            size=_int_value(torrent.get("size")),
            downloaded=_int_value(torrent.get("downloaded")),
            download_speed=_int_value(torrent.get("dlspeed")),
            upload_speed=_int_value(torrent.get("upspeed")),
            eta=_int_value(torrent.get("eta")),
            seeds=_int_value(torrent.get("num_seeds")),
            peers=_int_value(torrent.get("num_leechs")),
            save_path=str(torrent.get("save_path", "")),
        )

    def startup_download_results(self, output_dir: Path) -> list[DownloadResult]:
        active_states = frozenset(("downloading", "forcedDL", "queuedDL", "metaDL", "checkingDL"))
        immediate_states = frozenset(("stalledDL",))
        response = self._api_get("/api/v2/torrents/info")
        results: list[DownloadResult] = []

        for torrent in response.json():
            state = torrent.get("state")
            if state not in active_states and state not in immediate_states:
                continue

            info_hash = str(torrent.get("hash", ""))
            if state in active_states:
                if not info_hash:
                    continue
                self._wait_for_completion(info_hash)
                self._remove_torrent(info_hash)

            results.append(self._download_result_from_torrent(torrent, output_dir))

        return results

    def _download_result_from_torrent(self, torrent: dict, output_dir: Path) -> DownloadResult:
        source = self._torrent_source(torrent)
        return DownloadResult(
            magnet=source,
            files=self._torrent_files(torrent, output_dir),
            input=source,
        )

    def _torrent_source(self, torrent: dict) -> str:
        for key in ("magnet_uri", "hash", "name"):
            value = torrent.get(key)
            if value:
                return str(value)
        return ""

    def _torrent_files(self, torrent: dict, output_dir: Path) -> list[Path]:
        candidates: list[Path] = []
        for key in ("content_path",):
            value = torrent.get(key)
            if value:
                candidates.append(Path(str(value)))

        save_path = torrent.get("save_path")
        name = torrent.get("name")
        if save_path and name:
            candidates.append(Path(str(save_path)) / str(name))
        if name:
            candidates.append(output_dir / str(name))

        seen: set[Path] = set()
        for candidate in candidates:
            if not candidate.is_absolute():
                candidate = output_dir / candidate
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                return [candidate]
            if candidate.is_dir():
                return sorted((path for path in candidate.rglob("*") if path.is_file()), key=lambda path: str(path))
        return []

    def _find_new_hash(self, existing_hashes: set[str]) -> str | None:
        for attempt in range(10):
            time.sleep(1)
            try:
                response = self._api_get("/api/v2/torrents/info")
                current = {t["hash"]: t for t in response.json()}
                for h, t in current.items():
                    if h not in existing_hashes and t.get("state") not in ("unknown",):
                        self._log(f"hash detected on attempt {attempt + 1}")
                        return h
            except Exception:
                time.sleep(1)
        self._log("hash detection timed out after 10 attempts")
        return None

    def _wait_for_completion(self, info_hash: str) -> None:
        completed_states = frozenset(("pausedUP", "uploading", "stalledUP", "queuedUP", "forcedUP", "checkingUP"))
        no_seed_count = 0
        last_log_progress = -1.0
        while True:
            try:
                response = self._api_get("/api/v2/torrents/info", params={"hashes": info_hash})
                torrents = response.json()
                if not torrents:
                    raise DownloadError("qBittorrent torrent disappeared")

                torrent = torrents[0]
                state = torrent.get("state", "")
                progress = torrent.get("progress", 0)

                if progress - last_log_progress >= 0.05 or state != "downloading":
                    dlspeed = torrent.get("dlspeed", 0)
                    seeds = torrent.get("num_seeds", 0)
                    size = torrent.get("size", 0)
                    amount_left = torrent.get("amount_left", 0)
                    name = torrent.get("name", "")
                    speed_str = self._fmt_speed(dlspeed)
                    size_str = self._fmt_size(size)
                    left_str = self._fmt_size(amount_left)
                    self._log(f"  {progress*100:.1f}%  {speed_str}/s  seeds={seeds}  left={left_str}/{size_str}  {state}  {name}")
                    last_log_progress = progress

                if state in ("error", "missingFiles"):
                    raise DownloadError(f"qBittorrent torrent error state: {state}")
                if state in completed_states and progress >= 1.0:
                    return
                if self._has_no_active_seeds(torrent):
                    no_seed_count += 1
                    if no_seed_count >= self.no_seed_checks:
                        self._log(f"no active seeds for {self.no_seed_checks} polls, removing")
                        self._remove_torrent(info_hash)
                        raise DownloadError("qBittorrent torrent has no active seeds")
                else:
                    no_seed_count = 0
            except DownloadError:
                raise
            except Exception:
                pass
            time.sleep(self.poll_interval)

    @staticmethod
    def _fmt_speed(bps: object) -> str:
        try:
            b = int(bps)
        except (TypeError, ValueError):
            return "0 B"
        if b >= 1048576:
            return f"{b / 1048576:.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.0f} KB"
        return f"{b} B"

    @staticmethod
    def _fmt_size(b: object) -> str:
        try:
            b = int(b)
        except (TypeError, ValueError):
            return "0 B"
        if b >= 1073741824:
            return f"{b / 1073741824:.1f} GB"
        if b >= 1048576:
            return f"{b / 1048576:.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.0f} KB"
        return f"{b} B"

    def _has_no_active_seeds(self, torrent: dict) -> bool:
        seeds = torrent.get("num_seeds")
        if seeds is None:
            return False
        try:
            return int(seeds) <= 0 and torrent.get("progress", 0) < 1.0
        except (TypeError, ValueError):
            return False


def _int_value(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
