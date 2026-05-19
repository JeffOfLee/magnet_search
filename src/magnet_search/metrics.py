from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class MetricsRun:
    run_id: str
    command: str
    status: str
    stage: str
    started_at: float
    updated_at: float
    finished_at: float | None = None
    error: str = ""


@dataclass(frozen=True)
class MetricsCounters:
    run_id: str
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    skipped_items: int = 0
    downloaded_files: int = 0
    uploaded_files: int = 0
    bytes_downloaded: int = 0
    bytes_uploaded: int = 0
    items_per_second: float = 0.0
    bytes_per_second: float = 0.0
    eta_seconds: int = 0


@dataclass(frozen=True)
class MetricsItem:
    item_id: str
    name: str = ""
    source: str = ""
    state: str = ""
    progress: float = 0.0
    size_bytes: int = 0
    downloaded_bytes: int = 0
    download_speed_bytes: int = 0
    upload_speed_bytes: int = 0
    eta_seconds: int = 0
    seeds: int = 0
    peers: int = 0
    save_path: str = ""
    updated_at: float = 0.0


@dataclass(frozen=True)
class MetricsSnapshot:
    run: MetricsRun
    metrics: MetricsCounters
    items: list[MetricsItem]


class MetricsTracker:
    def __init__(
        self,
        db_path: Path,
        command: str,
        time_func: Callable[[], float] | None = None,
        run_id: str | None = None,
    ):
        self.db_path = db_path
        self.command = command
        self.time_func = time_func or time.time
        self.run_id = run_id or str(uuid.uuid4())
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        started_at = self.time_func()

        with self._connect() as conn:
            _initialize_schema(conn)
            conn.execute(
                """
                insert into runs (run_id, command, status, stage, started_at, updated_at, finished_at, error)
                values (?, ?, 'running', '', ?, ?, null, '')
                """,
                (self.run_id, self.command, started_at, started_at),
            )
            conn.execute("insert into run_metrics (run_id) values (?)", (self.run_id,))

    def update(
        self,
        *,
        stage: str | None = None,
        total_items: int | None = None,
        completed_items: int | None = None,
        failed_items: int | None = None,
        skipped_items: int | None = None,
        downloaded_files: int | None = None,
        uploaded_files: int | None = None,
        bytes_downloaded: int | None = None,
        bytes_uploaded: int | None = None,
        items_per_second: float | None = None,
        bytes_per_second: float | None = None,
        eta_seconds: int | None = None,
    ) -> None:
        now = self.time_func()
        with self._lock, self._connect() as conn:
            if stage is not None:
                conn.execute(
                    "update runs set stage = ?, updated_at = ? where run_id = ?",
                    (stage, now, self.run_id),
                )
            else:
                conn.execute("update runs set updated_at = ? where run_id = ?", (now, self.run_id))

            current = _load_counters(conn, self.run_id)
            values = {
                "total_items": _select(total_items, current.total_items),
                "completed_items": _select(completed_items, current.completed_items),
                "failed_items": _select(failed_items, current.failed_items),
                "skipped_items": _select(skipped_items, current.skipped_items),
                "downloaded_files": _select(downloaded_files, current.downloaded_files),
                "uploaded_files": _select(uploaded_files, current.uploaded_files),
                "bytes_downloaded": _select(bytes_downloaded, current.bytes_downloaded),
                "bytes_uploaded": _select(bytes_uploaded, current.bytes_uploaded),
                "items_per_second": _select(items_per_second, current.items_per_second),
                "bytes_per_second": _select(bytes_per_second, current.bytes_per_second),
                "eta_seconds": _select(eta_seconds, current.eta_seconds),
            }
            conn.execute(
                """
                update run_metrics
                set total_items = :total_items,
                    completed_items = :completed_items,
                    failed_items = :failed_items,
                    skipped_items = :skipped_items,
                    downloaded_files = :downloaded_files,
                    uploaded_files = :uploaded_files,
                    bytes_downloaded = :bytes_downloaded,
                    bytes_uploaded = :bytes_uploaded,
                    items_per_second = :items_per_second,
                    bytes_per_second = :bytes_per_second,
                    eta_seconds = :eta_seconds
                where run_id = :run_id
                """,
                {"run_id": self.run_id, **values},
            )

    def complete(self, stage: str = "done") -> None:
        self._finish("completed", stage=stage, error="")

    def fail(self, error: Exception | str, stage: str | None = None) -> None:
        self._finish("failed", stage=stage, error=str(error))

    def replace_items(self, items: list[MetricsItem]) -> None:
        now = self.time_func()
        with self._lock, self._connect() as conn:
            conn.execute("delete from run_items where run_id = ?", (self.run_id,))
            for item in items:
                item_time = item.updated_at or now
                conn.execute(
                    """
                    insert into run_items (
                        run_id, item_id, name, source, state, progress, size_bytes,
                        downloaded_bytes, download_speed_bytes, upload_speed_bytes,
                        eta_seconds, seeds, peers, save_path, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.run_id,
                        item.item_id,
                        item.name,
                        item.source,
                        item.state,
                        item.progress,
                        item.size_bytes,
                        item.downloaded_bytes,
                        item.download_speed_bytes,
                        item.upload_speed_bytes,
                        item.eta_seconds,
                        item.seeds,
                        item.peers,
                        item.save_path,
                        item_time,
                    ),
                )
            conn.execute("update runs set updated_at = ? where run_id = ?", (now, self.run_id))

    def to_dict(self) -> dict[str, object]:
        snapshot = load_metrics_snapshot(self.db_path, self.run_id)
        if snapshot is None:
            return {}
        return snapshot_to_dict(snapshot)

    def _finish(self, status: str, stage: str | None, error: str) -> None:
        now = self.time_func()
        with self._lock, self._connect() as conn:
            current_stage = stage
            if current_stage is None:
                current_stage = _load_run(conn, self.run_id).stage
            conn.execute(
                """
                update runs
                set status = ?, stage = ?, updated_at = ?, finished_at = ?, error = ?
                where run_id = ?
                """,
                (status, current_stage, now, now, error, self.run_id),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


def load_metrics_snapshot(db_path: Path, run_id: str | None = None) -> MetricsSnapshot | None:
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        _initialize_schema(conn)
        if run_id is None:
            row = conn.execute("select run_id from runs order by updated_at desc limit 1").fetchone()
            if row is None:
                return None
            run_id = row[0]

        run = _load_run(conn, run_id)
        counters = _load_counters(conn, run_id)
        items = [
            MetricsItem(
                item_id=row["item_id"],
                name=row["name"],
                source=row["source"],
                state=row["state"],
                progress=row["progress"],
                size_bytes=row["size_bytes"],
                downloaded_bytes=row["downloaded_bytes"],
                download_speed_bytes=row["download_speed_bytes"],
                upload_speed_bytes=row["upload_speed_bytes"],
                eta_seconds=row["eta_seconds"],
                seeds=row["seeds"],
                peers=row["peers"],
                save_path=row["save_path"],
                updated_at=row["updated_at"],
            )
            for row in conn.execute(
                "select * from run_items where run_id = ? order by item_id",
                (run_id,),
            )
        ]
        return MetricsSnapshot(run=run, metrics=counters, items=items)


def snapshot_to_dict(snapshot: MetricsSnapshot) -> dict[str, object]:
    return {
        "run": asdict(snapshot.run),
        "metrics": asdict(snapshot.metrics),
        "items": [asdict(item) for item in snapshot.items],
    }


def _initialize_schema(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        create table if not exists runs (
            run_id text primary key,
            command text not null,
            status text not null,
            stage text not null default '',
            started_at real not null,
            updated_at real not null,
            finished_at real,
            error text not null default ''
        )
        """
    )
    conn.execute(
        """
        create table if not exists run_metrics (
            run_id text primary key,
            total_items integer not null default 0,
            completed_items integer not null default 0,
            failed_items integer not null default 0,
            skipped_items integer not null default 0,
            downloaded_files integer not null default 0,
            uploaded_files integer not null default 0,
            bytes_downloaded integer not null default 0,
            bytes_uploaded integer not null default 0,
            items_per_second real not null default 0,
            bytes_per_second real not null default 0,
            eta_seconds integer not null default 0,
            foreign key (run_id) references runs(run_id) on delete cascade
        )
        """
    )
    conn.execute(
        """
        create table if not exists run_items (
            run_id text not null,
            item_id text not null,
            name text not null default '',
            source text not null default '',
            state text not null default '',
            progress real not null default 0,
            size_bytes integer not null default 0,
            downloaded_bytes integer not null default 0,
            download_speed_bytes integer not null default 0,
            upload_speed_bytes integer not null default 0,
            eta_seconds integer not null default 0,
            seeds integer not null default 0,
            peers integer not null default 0,
            save_path text not null default '',
            updated_at real not null,
            primary key (run_id, item_id),
            foreign key (run_id) references runs(run_id) on delete cascade
        )
        """
    )


def _load_run(conn: sqlite3.Connection, run_id: str) -> MetricsRun:
    conn.row_factory = sqlite3.Row
    row = conn.execute("select * from runs where run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"metrics run not found: {run_id}")
    return MetricsRun(
        run_id=row["run_id"],
        command=row["command"],
        status=row["status"],
        stage=row["stage"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
        error=row["error"],
    )


def _load_counters(conn: sqlite3.Connection, run_id: str) -> MetricsCounters:
    conn.row_factory = sqlite3.Row
    row = conn.execute("select * from run_metrics where run_id = ?", (run_id,)).fetchone()
    if row is None:
        return MetricsCounters(run_id=run_id)
    return MetricsCounters(
        run_id=row["run_id"],
        total_items=row["total_items"],
        completed_items=row["completed_items"],
        failed_items=row["failed_items"],
        skipped_items=row["skipped_items"],
        downloaded_files=row["downloaded_files"],
        uploaded_files=row["uploaded_files"],
        bytes_downloaded=row["bytes_downloaded"],
        bytes_uploaded=row["bytes_uploaded"],
        items_per_second=row["items_per_second"],
        bytes_per_second=row["bytes_per_second"],
        eta_seconds=row["eta_seconds"],
    )


def _select(value, default):
    return default if value is None else value
