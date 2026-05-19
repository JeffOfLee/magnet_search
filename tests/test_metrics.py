import sqlite3

from magnet_search.metrics import MetricsItem, MetricsTracker, load_metrics_snapshot


def test_metrics_tracker_initializes_schema_and_records_run_status(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    clock_values = iter([100.0, 101.0, 102.0])
    tracker = MetricsTracker(metrics_db, "search", time_func=lambda: next(clock_values), run_id="run-1")

    tracker.update(stage="searching", total_items=2, completed_items=1, bytes_downloaded=2048)
    tracker.complete(stage="done")

    with sqlite3.connect(metrics_db) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type = 'table'")}
        run = conn.execute("select run_id, command, status, stage, started_at, finished_at from runs").fetchone()
        metrics = conn.execute(
            "select total_items, completed_items, bytes_downloaded from run_metrics where run_id = 'run-1'"
        ).fetchone()

    assert {"runs", "run_metrics", "run_items"}.issubset(tables)
    assert run == ("run-1", "search", "completed", "done", 100.0, 102.0)
    assert metrics == (2, 1, 2048)


def test_metrics_tracker_replaces_per_item_snapshots(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    tracker = MetricsTracker(metrics_db, "download", run_id="run-2")

    tracker.replace_items(
        [
            MetricsItem(
                item_id="hash-1",
                name="first",
                source="magnet:?one",
                state="downloading",
                progress=0.25,
                size_bytes=100,
                downloaded_bytes=25,
                download_speed_bytes=10,
                upload_speed_bytes=1,
                eta_seconds=8,
                seeds=3,
                peers=4,
                save_path="/downloads",
            )
        ]
    )
    tracker.replace_items(
        [
            MetricsItem(
                item_id="hash-1",
                name="first",
                source="magnet:?one",
                state="downloading",
                progress=0.5,
            ),
            MetricsItem(
                item_id="hash-2",
                name="second",
                source="magnet:?two",
                state="pausedDL",
                progress=0.0,
            ),
        ]
    )

    snapshot = load_metrics_snapshot(metrics_db, run_id="run-2")

    assert snapshot is not None
    assert [item.item_id for item in snapshot.items] == ["hash-1", "hash-2"]
    assert snapshot.items[0].progress == 0.5
    assert snapshot.items[0].state == "downloading"


def test_metrics_snapshot_selects_latest_updated_run(tmp_path):
    metrics_db = tmp_path / "metrics.sqlite"
    first = MetricsTracker(metrics_db, "search", time_func=lambda: 100.0, run_id="first")
    second = MetricsTracker(metrics_db, "download", time_func=lambda: 200.0, run_id="second")
    first.update(stage="old")
    second.update(stage="new", total_items=3)

    snapshot = load_metrics_snapshot(metrics_db)

    assert snapshot is not None
    assert snapshot.run.run_id == "second"
    assert snapshot.run.command == "download"
    assert snapshot.metrics.total_items == 3
