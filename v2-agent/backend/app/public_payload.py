"""Public response projection for model-backed analysis payloads."""

from __future__ import annotations

import json
import re
from typing import Any


INTERNAL_KEYS = {
    "activeprovider",
    "backend",
    "detector",
    "detectorversion",
    "endpoint",
    "engine",
    "engineversion",
    "executionprovider",
    "hardware",
    "inferenceaudit",
    "internalendpoint",
    "localizationmodel",
    "localizationmodelrevision",
    "modelid",
    "modelname",
    "modelprofile",
    "modelprofiles",
    "modelrevision",
    "modelrun",
    "modelversion",
    "providermodel",
    "rawevidence",
    "remoteendpoint",
    "remoteevidence",
    "runtime",
}
MODEL_TERM_RE = re.compile(
    r"(?i)(?:\bDINOv?3?\b|\bViT(?:[-_ ]?\d+[A-Za-z]*)?\b|\bONNX(?:Runtime)?\b|"
    r"\b(?:CPU|CUDA)ExecutionProvider\b|\bCUDA\b|\bYOLO(?:v?\d+[A-Za-z]*)?\b|"
    r"\bRapidOCR\b|\bFAISS\b|\bCLIP\b|\bRealGuard(?:[-_ ]?v?\d+)?\b|"
    r"[A-Za-z0-9_.-]+/(?:dino|yolo|clip)[A-Za-z0-9_./-]*)"
)


def key_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def sanitize(value: Any, *, field_name: str = "", parent_name: str = "") -> Any:
    """Strip runtime identity while retaining user-owned evidence and attribution."""
    if isinstance(value, dict):
        clean = {}
        for key, child in value.items():
            key_text = str(key)
            normalized = key_name(key_text)
            if normalized in {"allmetadata", "metadata"}:
                clean[key] = json.loads(json.dumps(child, ensure_ascii=False, default=str))
                continue
            if normalized in INTERNAL_KEYS:
                continue
            # Generator attribution is evidence about the submitted media, not
            # the identity of the detector serving this request.
            if normalized == "model" and key_name(field_name) != "generatorattribution":
                continue
            clean[key] = sanitize(child, field_name=key_text, parent_name=field_name)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitize(item, field_name=field_name, parent_name=parent_name) for item in value]
    if isinstance(value, str):
        normalized_field = key_name(field_name)
        if normalized_field not in {
            "filename",
            "name",
            "imageurl",
            "preview",
            "thumbnail",
            "url",
            "reporturl",
        }:
            return MODEL_TERM_RE.sub("\u5185\u90e8\u5206\u6790\u670d\u52a1", value)
    return value
