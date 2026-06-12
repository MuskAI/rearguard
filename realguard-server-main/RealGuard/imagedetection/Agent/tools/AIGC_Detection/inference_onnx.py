import os

import numpy as np
import onnxruntime as ort
from PIL import Image


current_dir = os.path.dirname(os.path.abspath(__file__))

session = None
input_name = None
output_name = None


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


def predict(img_path):
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
