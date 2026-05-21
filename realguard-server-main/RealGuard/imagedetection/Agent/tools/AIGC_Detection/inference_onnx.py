import os

# 避免 albumentations 在检测启动时联网检查版本，干扰本地检测日志。
os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

import onnxruntime as ort
import numpy as np
import torch
from PIL import Image

from .data.transform_official import create_val_transforms

current_dir = os.path.dirname(os.path.abspath(__file__))

session = None
input_name = None
output_name = None
transform = None

def _lazy_init():
    global session, input_name, output_name, transform
    if session is not None:
        return

    onnx_path = os.path.join(current_dir, "model_deploy.onnx")

    providers = ['CPUExecutionProvider']

    print(f"  [模型加载] 正在加载 ONNX 模型...")
    session = ort.InferenceSession(onnx_path, providers=providers)
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    transform = create_val_transforms(size=224, is_crop=True)
    print(f"  [模型加载] 完成。")

def preprocess(img_path):
    _lazy_init()
    img = Image.open(img_path).convert("RGB")
    img_np = np.array(img)

    out = transform(image=img_np)
    img_tensor = out["image"]

    if isinstance(img_tensor, torch.Tensor):
        img_numpy = img_tensor.numpy()
    else:
        if img_tensor.ndim == 3 and img_tensor.shape[2] == 3:
            img_numpy = img_tensor.transpose(2, 0, 1)
        else:
            img_numpy = img_tensor

    img_numpy = img_numpy.astype(np.float32)
    if img_numpy.ndim == 3:
        img_numpy = img_numpy[None, :, :, :]

    return img_numpy

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
