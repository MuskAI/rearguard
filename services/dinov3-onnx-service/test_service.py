import io
import unittest

from PIL import Image

import service


class FakeDetector:
    active_providers = ["CPUExecutionProvider"]

    def predict_pil(self, image):
        assert image.mode == "RGB"
        return {
            "fake_probability": 0.87,
            "real_probability": 0.13,
        }


def _png():
    output = io.BytesIO()
    Image.new("RGB", (16, 12), (20, 40, 60)).save(output, format="PNG")
    return output.getvalue()


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.previous_detector = service.runtime.detector
        self.previous_state = service.runtime.state
        service.runtime.detector = FakeDetector()
        service.runtime.state = "ready"
        self.client = service.app.test_client()

    def tearDown(self):
        service.runtime.detector = self.previous_detector
        service.runtime.state = self.previous_state

    def test_health_reports_loaded_candidate(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["serviceOk"])
        self.assertFalse(payload["verdictReady"])

    def test_image_response_matches_realguard_contract(self):
        response = self.client.post(
            "/image",
            data={"image_file": (io.BytesIO(_png()), "sample.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["code"], 200)
        self.assertEqual(payload["data"]["final_label"], "AI生成图像")
        self.assertEqual(payload["data"]["fake_percentage"], 87.0)
        self.assertEqual(payload["data"]["decisionStatus"], "review_only")
        self.assertEqual(
            payload["data"]["remote_evidence"]["visibleWatermarkPrecheck"]["status"],
            "unavailable",
        )
        self.assertFalse(payload["data"]["remote_evidence"]["modelDecision"]["ready"])

    def test_image_rejects_missing_upload(self):
        response = self.client.post("/image")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
