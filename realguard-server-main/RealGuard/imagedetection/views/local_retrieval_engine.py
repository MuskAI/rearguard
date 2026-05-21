import json
import os
import threading
from typing import Dict, List, Tuple

import faiss
import numpy as np
import torch
import cv2
from PIL import Image


DEMO_PROJECT_ROOT = os.environ.get('DEMO_PROJECT_ROOT', '/media/disk2/hl/code/demo').strip()
VIDEO_SAMPLE_FPS = float(os.environ.get('LOCAL_VIDEO_SAMPLE_FPS', '1'))
TOP_K_HARD_LIMIT = int(os.environ.get('LOCAL_RETRIEVE_TOPK_LIMIT', '200'))

_CLIP_MODEL = None
_CLIP_PREPROCESS = None
_MODEL_LOCK = threading.Lock()
_ENGINE_CACHE: Dict[Tuple[str, str], "DatasetEngine"] = {}
_CACHE_LOCK = threading.Lock()


def _ensure_demo_import_path():
    if DEMO_PROJECT_ROOT and DEMO_PROJECT_ROOT not in os.sys.path:
        os.sys.path.insert(0, DEMO_PROJECT_ROOT)


def _load_clip_model():
    global _CLIP_MODEL, _CLIP_PREPROCESS
    if _CLIP_MODEL is not None and _CLIP_PREPROCESS is not None:
        return _CLIP_MODEL, _CLIP_PREPROCESS

    with _MODEL_LOCK:
        if _CLIP_MODEL is not None and _CLIP_PREPROCESS is not None:
            return _CLIP_MODEL, _CLIP_PREPROCESS

        _ensure_demo_import_path()
        from CLIP.clip import clip  # noqa: WPS433

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model, preprocess = clip.load("ViT-B/32", device=device)
        model.eval()
        _CLIP_MODEL = model
        _CLIP_PREPROCESS = preprocess
        return _CLIP_MODEL, _CLIP_PREPROCESS


def _encode_image(image_path: str) -> np.ndarray:
    model, preprocess = _load_clip_model()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    img = Image.open(image_path).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = model.encode_image(tensor).cpu().numpy().astype(np.float32)
    return feat


def _sample_video_frames(video_path: str, fps: float = 1.0) -> List[np.ndarray]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    if not src_fps or src_fps <= 0:
        src_fps = 25.0
    step = max(int(round(src_fps / max(fps, 0.1))), 1)
    idx = 0
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(rgb)
        idx += 1
    cap.release()
    return frames


def _encode_video(video_path: str) -> np.ndarray:
    model, preprocess = _load_clip_model()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    frames = _sample_video_frames(video_path, VIDEO_SAMPLE_FPS)
    if not frames:
        return np.zeros((1, 512), dtype=np.float32)

    feats = []
    with torch.no_grad():
        for frame in frames:
            img = Image.fromarray(frame)
            tensor = preprocess(img).unsqueeze(0).to(device)
            feat = model.encode_image(tensor).cpu().numpy().astype(np.float32)
            feats.append(feat[0])
    arr = np.array(feats, dtype=np.float32)
    pooled = np.mean(arr, axis=0, keepdims=True)
    return pooled


class DatasetEngine:
    def __init__(self, search_type: str, dataset: str, root_path: str):
        self.search_type = search_type
        self.dataset = dataset
        self.root_path = root_path
        self.dataset_dir = os.path.join(root_path, dataset)

        id2file_path = os.path.join(self.dataset_dir, 'id2file.json')
        if not os.path.isfile(id2file_path):
            raise FileNotFoundError(f"id2file.json not found: {id2file_path}")
        with open(id2file_path, 'r', encoding='utf-8') as fp:
            mapping = json.load(fp)
        self.image_keys = np.array(list(mapping.values()))

        index_path = os.path.join(self.dataset_dir, 'faissIndex', f'{dataset}_clip_features.index')
        if not os.path.isfile(index_path):
            raise FileNotFoundError(f"faiss index not found: {index_path}")
        cpu_index = faiss.read_index(index_path)
        if torch.cuda.is_available():
            res = faiss.StandardGpuResources()
            self.index = faiss.index_cpu_to_gpu(res, 0, cpu_index)
        else:
            self.index = cpu_index

    def _build_rel_id(self, key_value: str, default_subdir: str) -> str:
        key = str(key_value or '').replace('\\', '/').lstrip('/')
        # id2file 里若已包含子目录（如 ImageData/xxx.jpg），直接拼 dataset 前缀
        if '/' in key:
            return f"{self.dataset}/{key}"
        # 否则按默认子目录补齐（常见于视频 id2file 仅含文件名）
        return f"{self.dataset}/{default_subdir}/{key}"

    def search(self, query_path: str, top_k: int) -> List[dict]:
        top_k = max(1, min(int(top_k), TOP_K_HARD_LIMIT))
        if self.search_type == 'image':
            q = _encode_image(query_path)
            suffix = 'ImageData'
        else:
            q = _encode_video(query_path)
            suffix = 'VideoData'
        distances, indices = self.index.search(q, top_k)
        result = []
        for rank, (idx, score) in enumerate(zip(indices[0].tolist(), distances[0].tolist()), start=1):
            if idx < 0 or idx >= len(self.image_keys):
                continue
            rel_id = self._build_rel_id(self.image_keys[idx], suffix)
            result.append({
                'id': rel_id,
                'rank': rank,
                'score': float(score),
                'index': str(rank - 1),
                'product': {
                    'product_name': '',
                    'illegal_type': '',
                    'illegal_basis': '',
                    'product_images': rel_id,
                }
            })
        return result


def get_engine(search_type: str, dataset: str, root_path: str) -> DatasetEngine:
    key = (search_type, dataset)
    with _CACHE_LOCK:
        if key in _ENGINE_CACHE:
            return _ENGINE_CACHE[key]
        engine = DatasetEngine(search_type=search_type, dataset=dataset, root_path=root_path)
        _ENGINE_CACHE[key] = engine
        return engine

