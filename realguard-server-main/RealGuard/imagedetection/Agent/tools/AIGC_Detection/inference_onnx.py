import os

import numpy as np
import onnxruntime as ort
import requests
from PIL import Image


current_dir = os.path.dirname(os.path.abspath(__file__))

session = None
input_name = None
output_name = None

REMOTE_INFERENCE_URL = os.environ.get("REALGUARD_REMOTE_INFERENCE_URL", "").strip()
REMOTE_INFERENCE_TOKEN = os.environ.get("REALGUARD_MODEL_INTERNAL_TOKEN", "").strip()
REMOTE_INFERENCE_TIMEOUT = float(os.environ.get("REALGUARD_REMOTE_INFERENCE_TIMEOUT", "120"))
REMOTE_REQUIRE_CUDA = os.environ.get("REALGUARD_REMOTE_REQUIRE_CUDA", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _lazy_init():
    global session, input_name, output_name
    if session is not None:
        return

    onnx_path = os.path.join(current_dir, "model_deploy.onnx")
    providers = ["CPUExecutionProvider"]

    print("  [模型加载] 正在加载 ONNX 模型...")
    session = ort.InferenceSession(onnx_path, providers=providers)
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    print("  [模型加载] 完成。")


def _center_pad_and_crop(img, size=224):
    width, height = img.size
    canvas_width = max(width, size)
    canvas_height = max(height, size)
    if canvas_width != width or canvas_height != height:
        canvas = Image.new("RGB", (canvas_width, canvas_height), (0, 0, 0))
        canvas.paste(img, ((canvas_width - width) // 2, (canvas_height - height) // 2))
        img = canvas

    left = max((img.size[0] - size) // 2, 0)
    top = max((img.size[1] - size) // 2, 0)
    return img.crop((left, top, left + size, top + size))


def preprocess(img_path):
    img = Image.open(img_path).convert("RGB")
    img = _center_pad_and_crop(img, size=224)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    mean = np.array((0.485, 0.456, 0.406), dtype=np.float32)
    std = np.array((0.229, 0.224, 0.225), dtype=np.float32)
    arr = (arr - mean) / std
    return arr.transpose(2, 0, 1)[None, :, :, :].astype(np.float32)


def _predict_remote(img_path):
    headers = {}
    if REMOTE_INFERENCE_TOKEN:
        headers["X-RealGuard-Internal-Token"] = REMOTE_INFERENCE_TOKEN

    filename = os.path.basename(str(img_path)) or "image.png"
    with open(img_path, "rb") as image_file:
        response = requests.post(
            REMOTE_INFERENCE_URL,
            files={"image_file": (filename, image_file, "application/octet-stream")},
            headers=headers,
            timeout=(5, REMOTE_INFERENCE_TIMEOUT),
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Remote inference returned HTTP {response.status_code} without JSON"
        ) from exc
    if response.status_code != 200 or payload.get("code") != 200:
        message = payload.get("msg") or f"HTTP {response.status_code}"
        raise RuntimeError(f"Remote inference failed: {message}")

    data = payload.get("data") or {}
    runtime = data.get("runtime") or {}
    if REMOTE_REQUIRE_CUDA and runtime.get("activeProvider") != "CUDAExecutionProvider":
        raise RuntimeError(
            "Remote inference is not using CUDAExecutionProvider: "
            f"{runtime.get('activeProvider') or 'unknown'}"
        )

    try:
        probability = float(data["fakeProbability"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Remote inference response has no valid fakeProbability") from exc
    if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise RuntimeError(f"Remote inference returned an invalid probability: {probability}")
    return probability


def predict(img_path):
    if REMOTE_INFERENCE_URL:
        return _predict_remote(img_path)

    _lazy_init()
    input_tensor = preprocess(img_path)

    logits = session.run([output_name], {input_name: input_tensor})[0]

    def softmax(x):
        e_x = np.exp(x - np.max(x))
        return e_x / e_x.sum(axis=1, keepdims=True)

    probs = softmax(logits)
    prob_fake = float(probs[0, 1])
    return prob_fake


if __name__ == "__main__":
    img_path = "/media/disk2/gdq/Agent/test_photo/原像素.jpg"

    prob = predict(img_path)
    print(f"ONNX Prob: {prob}")
