from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.views import detection  # noqa: E402


def test_existing_primary_record_accepts_same_task_id_as_idempotent_replay(monkeypatch):
    api_json = {
        "code": 200,
        "data": {
            "data_itemid": 23,
            "filename": "stored.png",
        },
    }
    monkeypatch.setattr(
        detection,
        "_local_detection_record",
        lambda itemid: {
            "itemid": itemid,
            "filename": "stored.png",
            "Userid": None,
            "owner_account_uuid": None,
            "phone": "",
            "openid": "guest-existing",
            "developer_task_id": "job-existing",
        },
    )
    monkeypatch.setattr(detection.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(
        detection,
        "excute_detection_sql",
        lambda *args, **kwargs: pytest.fail("an idempotent replay must not rewrite the task link"),
    )

    itemid = detection._ensure_local_primary_record(
        api_json,
        b"image-bytes",
        "upload.png",
        "guest-existing",
        "",
        {"Userid": None, "openid": "guest-existing"},
        "job-existing",
    )

    assert itemid == 23
    assert api_json["data"]["image_url"] == "/api/media/image/23"
