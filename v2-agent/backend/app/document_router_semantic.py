from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import threading
from typing import Any, Protocol, Sequence

import numpy as np
from PIL import Image, ImageOps


MODEL_ID = "wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M"
MODEL_FILENAME = "model_int8.onnx"
TOKENIZER_FILENAME = "tokenizer.json"

_PROMPTS: dict[str, tuple[str, ...]] = {
    "photograph": (
        "a natural photograph taken by a camera",
        "a realistic photo showing a person, animal, object, or place",
    ),
    "artwork": (
        "a standalone digital artwork, illustration, painting, or synthetic image",
        "a complete visual scene that could be AI generated",
    ),
    "logo": ("a company logo, brand mark, or wordmark",),
    "icon": ("a simple user interface icon, symbol, badge, or emoji",),
    "chart": ("a scientific chart, data plot, graph, or visualization",),
    "diagram": ("a technical diagram, flowchart, architecture diagram, or infographic",),
    "interface": ("a screenshot of software, a website, an app, or a user interface",),
    "table": ("a table, spreadsheet, or grid of numbers",),
    "text_document": ("a page of text, scanned document, slide, formula, or equation",),
    "decoration": ("a decorative shape, texture, border, divider, or background pattern",),
    "machine_code": ("a QR code, barcode, or machine-readable marker",),
}

_CATEGORY_LABELS = {
    "photograph": "自然照片",
    "artwork": "完整视觉作品",
    "logo": "Logo 或品牌标识",
    "icon": "图标或符号",
    "chart": "图表或数据图",
    "diagram": "流程图或技术示意图",
    "interface": "软件或网页截图",
    "table": "表格",
    "text_document": "文字或文档页面",
    "decoration": "装饰或背景图层",
    "machine_code": "二维码或条形码",
}

MEANINGFUL_CATEGORIES = frozenset({"photograph", "artwork"})


class SemanticAsset(Protocol):
    data: bytes


@dataclass(frozen=True, slots=True)
class SemanticPrediction:
    category: str
    category_label: str
    confidence: float
    meaningful_score: float
    scores: dict[str, float]
    model: str = MODEL_ID


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - np.max(values, axis=1, keepdims=True)
    exponentials = np.exp(shifted)
    return exponentials / np.maximum(np.sum(exponentials, axis=1, keepdims=True), 1e-12)


class TinyClipSemanticClassifier:
    """Lazy, CPU-friendly semantic classifier used after deterministic filters."""

    def __init__(self, model_dir: str | Path | None = None) -> None:
        configured = model_dir or os.getenv("JIANZHEN_DOCUMENT_ROUTER_MODEL_DIR", "")
        if configured:
            self.model_dir = Path(configured).expanduser()
        else:
            self.model_dir = Path(__file__).resolve().parents[1] / "models" / "document-router" / "tinyclip-int8"
        self._lock = threading.Lock()
        self._session: Any | None = None
        self._token_ids: np.ndarray | None = None
        self._attention_mask: np.ndarray | None = None
        self._prompt_categories: tuple[str, ...] = ()
        self._load_error = ""

    @property
    def available(self) -> bool:
        return (self.model_dir / MODEL_FILENAME).is_file() and (
            self.model_dir / TOKENIZER_FILENAME
        ).is_file()

    @property
    def status(self) -> dict[str, Any]:
        if self._session is not None:
            state = "ready"
        elif self.available:
            state = "available"
        else:
            state = "missing"
        return {
            "state": state,
            "model": MODEL_ID,
            "runtime": "ONNX Runtime / INT8 / CPU",
            "modelDir": str(self.model_dir),
            "error": self._load_error or None,
        }

    def _load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            if not self.available:
                raise FileNotFoundError(f"TinyCLIP router model is missing from {self.model_dir}")
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                options = ort.SessionOptions()
                options.intra_op_num_threads = max(
                    1, int(os.getenv("JIANZHEN_DOCUMENT_ROUTER_INTRA_THREADS", "2"))
                )
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                session = ort.InferenceSession(
                    str(self.model_dir / MODEL_FILENAME),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                tokenizer = Tokenizer.from_file(str(self.model_dir / TOKENIZER_FILENAME))
                tokenizer.enable_truncation(max_length=77)
                tokenizer.enable_padding(
                    length=77,
                    pad_id=49407,
                    pad_token="<|endoftext|>",
                )
                prompt_categories: list[str] = []
                prompts: list[str] = []
                for category, category_prompts in _PROMPTS.items():
                    for prompt in category_prompts:
                        prompt_categories.append(category)
                        prompts.append(prompt)
                encoded = tokenizer.encode_batch(prompts)
                self._token_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
                self._attention_mask = np.asarray(
                    [item.attention_mask for item in encoded], dtype=np.int64
                )
                self._prompt_categories = tuple(prompt_categories)
                self._session = session
                self._load_error = ""
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                raise

    @staticmethod
    def _prepare_image(data: bytes) -> np.ndarray:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            scale = 224.0 / max(1, min(image.size))
            image = image.resize(
                (max(224, round(image.width * scale)), max(224, round(image.height * scale))),
                Image.Resampling.BICUBIC,
            )
            left = max(0, (image.width - 224) // 2)
            top = max(0, (image.height - 224) // 2)
            image = image.crop((left, top, left + 224, top + 224))
            values = np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 255.0
        mean = np.asarray([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)[:, None, None]
        std = np.asarray([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)[:, None, None]
        return (values - mean) / std

    def classify(self, assets: Sequence[SemanticAsset]) -> list[SemanticPrediction | None]:
        if not assets:
            return []
        self._load()
        assert self._session is not None
        assert self._token_ids is not None
        assert self._attention_mask is not None

        prepared: list[np.ndarray] = []
        valid_indexes: list[int] = []
        results: list[SemanticPrediction | None] = [None] * len(assets)
        for index, asset in enumerate(assets):
            try:
                prepared.append(self._prepare_image(asset.data))
                valid_indexes.append(index)
            except (OSError, SyntaxError, ValueError):
                continue
        if not prepared:
            return results

        category_names = tuple(_PROMPTS)
        prompt_indexes = {
            category: [
                index
                for index, prompt_category in enumerate(self._prompt_categories)
                if prompt_category == category
            ]
            for category in category_names
        }
        batch_size = max(1, min(64, int(os.getenv("JIANZHEN_DOCUMENT_ROUTER_BATCH_SIZE", "16"))))
        for start in range(0, len(prepared), batch_size):
            batch = np.stack(prepared[start : start + batch_size]).astype(np.float32, copy=False)
            logits = self._session.run(
                ["logits_per_image"],
                {
                    "input_ids": self._token_ids,
                    "attention_mask": self._attention_mask,
                    "pixel_values": batch,
                },
            )[0]
            category_logits = np.stack(
                [np.mean(logits[:, prompt_indexes[name]], axis=1) for name in category_names],
                axis=1,
            )
            probabilities = _softmax(category_logits)
            for offset, row in enumerate(probabilities):
                top_index = int(np.argmax(row))
                top_category = category_names[top_index]
                scores = {
                    category: round(float(row[index]), 4)
                    for index, category in enumerate(category_names)
                }
                meaningful_score = sum(scores[name] for name in MEANINGFUL_CATEGORIES)
                result_index = valid_indexes[start + offset]
                results[result_index] = SemanticPrediction(
                    category=top_category,
                    category_label=_CATEGORY_LABELS[top_category],
                    confidence=round(float(row[top_index]), 4),
                    meaningful_score=round(min(max(meaningful_score, 0.0), 1.0), 4),
                    scores=scores,
                )
        return results


_DEFAULT_CLASSIFIER = TinyClipSemanticClassifier()


def default_semantic_classifier() -> TinyClipSemanticClassifier:
    return _DEFAULT_CLASSIFIER
