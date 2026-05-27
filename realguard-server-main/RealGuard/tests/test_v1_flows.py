from io import BytesIO
from pathlib import Path
import json
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import api, detection, retrieve  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def client():
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _login_session(client, phone="13800000000"):
    with client.session_transaction() as sess:
        sess["user_info"] = {
            "Userid": 1,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        }


def test_api_password_login_sets_session(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "_authenticate_password_user",
        lambda phone, secret: {
            "Userid": 7,
            "username": "tester",
            "phone": phone,
            "openid": "openid-1",
        },
    )
    monkeypatch.setattr(api, "_sync_detection_user", lambda *args, **kwargs: None)

    response = client.post("/api/login/password", json={"phone": "13800000000", "secret": "hashed-pass"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["Userid"] == 7
    with client.session_transaction() as sess:
        assert sess["user_info"]["phone"] == "13800000000"


def test_guest_image_detect_returns_rewritten_url_and_then_blocks(client, monkeypatch):
    monkeypatch.setattr(detection, "_metadata_for_item", lambda itemid: {})
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda url, **kwargs: _FakeResponse(
            {
                "code": 200,
                "data": {
                    "data_itemid": 11,
                    "fake_percentage": 64.0,
                    "final_label": "AI生成图像",
                    "confidence": "高",
                    "image_url": "http://10.1.20.66:5000/static/uploads/guest/image/demo.png",
                    "filename": "demo.png",
                    "file_size": "12KB",
                    "img_format": "png",
                    "resolution": "256x256",
                },
            }
        ),
    )

    first = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert first.status_code == 200
    payload = first.get_json()
    assert payload["result"]["image_url"] == "/detection-static/uploads/guest/image/demo.png"
    with client.session_transaction() as sess:
        assert sess[detection.GUEST_DETECTION_SESSION_KEY] == 1

    second = client.post(
        "/image_upload/detect",
        data={"image": (BytesIO(b"fake-image"), "demo.png")},
        content_type="multipart/form-data",
    )

    assert second.status_code == 401
    assert "请登录后继续检测" in second.get_json()["message"]


def test_video_detect_logged_in_builds_public_media_url(client, monkeypatch):
    _login_session(client)
    monkeypatch.setattr(
        detection,
        "_backend_post",
        lambda url, **kwargs: _FakeResponse(
            {
                "code": 200,
                "data": {
                    "data_itemid": 21,
                    "fake_percentage": 83.0,
                    "real_percentage": 17.0,
                    "confidence": 0.91,
                    "final_label": "fake",
                    "frame_count": 48,
                    "encoder": "h264",
                    "meta": {
                        "file_size": "2.5MB",
                        "duration": "8s",
                        "resolution": "1280x720",
                        "video_format": "mp4",
                    },
                },
            }
        ),
    )

    monkeypatch.setattr(
        detection,
        "excute_detection_sql",
        lambda sql, params=None, fetch=True: [{
            "filename": "video.mp4",
            "openid": "openid-1",
            "phone": "13800000000",
        }] if sql == "SELECT * FROM video_data WHERE itemid = %s" else [],
    )

    response = client.post(
        "/video_upload/detect",
        data={"video_url": "https://example.com/video.mp4", "fast_mode": "1"},
    )

    assert response.status_code == 200
    payload = response.get_json()["result"]
    assert payload["final_label"] == "AI生成视频"
    assert payload["confidence"] == "高"
    assert payload["video_url"] == "/detection-static/uploads/openid-1/video/video.mp4"


def test_guest_image_report_downloads_attachment(client, monkeypatch):
    with client.session_transaction() as sess:
        sess["guest_openid"] = "guest-123"

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE itemid = %s AND openid = %s LIMIT 1":
            assert params == ("31", "guest-123")
            return [{
                "itemid": 31,
                "filename": "guest.png",
                "fake": 61.0,
                "clarity": "中",
                "file_size": "8KB",
                "img_format": "png",
                "resolution": "320x320",
                "openid": "guest-123",
                "createtime": "2026-05-27 14:00:00",
            }]
        if sql == "SELECT all_metadata FROM exif WHERE data_itemid = %s LIMIT 1":
            return []
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)

    response = client.get("/image_upload/report?itemid=31")

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "图像鉴伪报告" in response.get_data(as_text=True)
    assert "guest.png" in response.get_data(as_text=True)


def test_video_report_downloads_attachment_for_logged_user(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM video_data WHERE itemid = %s AND (phone = %s OR openid = %s) LIMIT 1":
            assert params == ("41", "13800000000", "openid-1")
            return [{
                "itemid": 41,
                "filename": "clip.mp4",
                "fake": 78.5,
                "final_label": "AI生成视频",
                "confidence": "高",
                "explanation": "检测到明显生成痕迹。",
                "duration": "10s",
                "resolution": "1280x720",
                "file_size": "4MB",
                "encoder": "h264",
                "frame_count": 55,
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 14:05:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(detection, "excute_detection_sql", fake_detection_sql)

    response = client.get("/video_upload/report?itemid=41")

    assert response.status_code == 200
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "视频鉴伪报告" in response.get_data(as_text=True)
    assert "clip.mp4" in response.get_data(as_text=True)


def test_retrieve_search_uses_selected_library_and_persists_history(client, monkeypatch, tmp_path):
    _login_session(client)
    insert_calls = []
    monkeypatch.setattr(retrieve, "current_dir", str(tmp_path))
    monkeypatch.setattr(retrieve, "list_retrieve_libraries", lambda search_type: ["libA"])
    monkeypatch.setattr(
        retrieve,
        "_build_local_retrieve_results",
        lambda **kwargs: [
            {
                "id": "libA/gallery/case1.png",
                "score": 0.87,
                "product": {"product_images": "libA/gallery/case1.png"},
            }
        ],
    )
    monkeypatch.setattr(retrieve, "get_now_str", lambda: "2026-05-27 13:00:00")
    monkeypatch.setattr(retrieve, "get_file_size_str", lambda path: "1KB")

    def fake_execute(sql, params=None, fetch=True):
        if "INSERT INTO retrieve_data" in sql:
            insert_calls.append((sql, params))
            return 1
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(retrieve, "excute_sql", fake_execute)

    response = client.post(
        "/retrieve/search",
        data={
            "image": (BytesIO(b"image-binary"), "query.png"),
            "search_type": "image",
            "dataset": "libA",
            "top_k": "5",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataset"] == "libA"
    assert payload["base_url"] == "/retrieve/library-file/image/"
    assert payload["results"][0]["id"].startswith("libA/")
    assert insert_calls
    _, params = insert_calls[0]
    assert params[2] == "image"
    assert json.loads(params[8])[0]["id"].startswith("libA/")


def test_history_detection_records_include_report_urls(client, monkeypatch):
    _login_session(client)

    def fake_detection_sql(sql, params=None, fetch=True):
        if sql == "SELECT * FROM data WHERE phone = %s ORDER BY createtime DESC":
            return [{
                "itemid": 51,
                "filename": "img.png",
                "fake": 51.5,
                "clarity": "中",
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 15:00:00",
            }]
        if sql == "SELECT * FROM video_data WHERE phone = %s ORDER BY createtime DESC":
            return [{
                "itemid": 61,
                "filename": "vid.mp4",
                "fake": 78.2,
                "final_label": "AI生成视频",
                "confidence": "高",
                "openid": "openid-1",
                "phone": "13800000000",
                "createtime": "2026-05-27 15:02:00",
            }]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(api, "excute_detection_sql", fake_detection_sql)

    image_response = client.get("/api/history/image-detections")
    video_response = client.get("/api/history/video-detections")

    assert image_response.status_code == 200
    assert video_response.status_code == 200
    assert image_response.get_json()["records"][0]["report_url"] == "/image_upload/report?itemid=51"
    assert video_response.get_json()["records"][0]["report_url"] == "/video_upload/report?itemid=61"
