from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import detection  # noqa: E402


@pytest.fixture
def client():
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_logged_in_feedback_is_scoped_to_current_owner(client, monkeypatch):
    captured = {}

    def fake_sql(sql, params=None, fetch=True):
        captured.update(sql=sql, params=params, fetch=fetch)
        return 1

    monkeypatch.setattr(detection, "excute_detection_sql", fake_sql)
    with client.session_transaction() as session:
        session["user_info"] = {
            "Userid": 7,
            "account_uuid": "11111111-1111-4111-8111-111111111111",
            "phone": "13800000000",
            "openid": "owner-openid",
        }

    response = client.post("/image_upload/feedback", json={"itemid": 31, "feedback": 1})

    assert response.status_code == 200
    assert response.get_json()["feedback"] == 1
    assert "Userid" not in captured["sql"]
    assert "owner_account_uuid = %s" in captured["sql"]
    assert captured["params"] == ("满意", 31, "11111111-1111-4111-8111-111111111111")
    assert captured["fetch"] is False


def test_guest_feedback_is_scoped_to_guest_session(client, monkeypatch):
    captured = {}

    def fake_sql(sql, params=None, fetch=True):
        captured.update(sql=sql, params=params, fetch=fetch)
        return 1

    monkeypatch.setattr(detection, "excute_detection_sql", fake_sql)
    with client.session_transaction() as session:
        session["guest_openid"] = "guest-session-1"

    response = client.post("/image_upload/feedback", json={"itemid": 45, "feedback": -1})

    assert response.status_code == 200
    assert response.get_json()["feedback"] == -1
    assert "Userid IS NULL" in captured["sql"]
    assert "openid = %s" in captured["sql"]
    assert captured["params"] == ("不满意", 45, "guest-session-1")


def test_guest_feedback_rejects_missing_guest_identity(client, monkeypatch):
    monkeypatch.setattr(
        detection,
        "excute_detection_sql",
        lambda *args, **kwargs: pytest.fail("database must not be touched without an owner"),
    )

    response = client.post("/image_upload/feedback", json={"itemid": 45, "feedback": -1})

    assert response.status_code == 401
    assert "访客会话" in response.get_json()["message"]
