import importlib.util
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_internal_testing_store.py"


def _module():
    spec = importlib.util.spec_from_file_location("internal_testing_migration", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_rewrites_paths_requeues_work_and_maps_model(tmp_path):
    migration = _module()
    root = tmp_path / "new-store"
    sample = root / "datasets" / "ds_1" / "sample.png"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"image")
    database = sqlite3.connect(root / "internal-testing.sqlite3")
    database.execute("CREATE TABLE samples (storage_path TEXT NOT NULL)")
    database.execute(
        "INSERT INTO samples VALUES (?)",
        ("/opt/realguard-data/internal-testing/datasets/ds_1/sample.png",),
    )
    database.commit()
    database.close()

    import_dir = root / "imports" / "imp_1234567890abcdef1234"
    payload = import_dir / "payloads" / "source.png"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"image")
    manifest = sqlite3.connect(import_dir / "manifest.sqlite3")
    manifest.execute("CREATE TABLE files (storage_path TEXT NOT NULL)")
    manifest.execute("CREATE TABLE chunks (part_path TEXT, status TEXT NOT NULL)")
    manifest.execute(
        "INSERT INTO files VALUES (?)",
        ("/opt/realguard-data/internal-testing/imports/imp_1234567890abcdef1234/payloads/source.png",),
    )
    manifest.commit()
    manifest.close()
    (import_dir / "session.json").write_text(json.dumps({
        "id": "imp_1234567890abcdef1234",
        "status": "processing",
        "model": {
            "endpoint": "http://127.0.0.1:15002/image",
            "healthUrl": "http://127.0.0.1:15002/health",
        },
    }), encoding="utf-8")

    result = migration.migrate(
        root,
        "/opt/realguard-data/internal-testing",
        {"http://127.0.0.1:15002": "http://127.0.0.1:5071"},
    )

    assert result["missingFileCount"] == 0
    assert result["requeuedSessions"] == 1
    connection = sqlite3.connect(root / "internal-testing.sqlite3")
    assert connection.execute("SELECT storage_path FROM samples").fetchone()[0] == str(sample)
    connection.close()
    state = json.loads((import_dir / "session.json").read_text(encoding="utf-8"))
    assert state["status"] == "queued"
    assert state["model"]["endpoint"] == "http://127.0.0.1:5071/image"
