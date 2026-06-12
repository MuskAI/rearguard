"""WAM (Watermark-Anything) sidecar reference implementation.

This is a small FastAPI service that wraps the
facebookresearch/watermark-anything model so the main RealGuard backend can
delegate WAM inference over HTTP without having torch + the 378 MB checkpoint
linked into the production runtime.

# Quickstart

    # On any machine with Python 3.10+ and torch installed:
    git clone https://github.com/facebookresearch/watermark-anything wam_repo
    cd wam_repo
    pip install -r requirements.txt
    mkdir -p checkpoints
    curl -L -o checkpoints/wam_mit.pth https://dl.fbaipublicfiles.com/watermark_anything/wam_mit.pth
    cp checkpoints/params.json checkpoints/  # already in repo

    # Then copy this file in and run:
    pip install fastapi uvicorn[standard]
    WAM_REPO_PATH=$(pwd) WAM_CHECKPOINT=$(pwd)/checkpoints/wam_mit.pth \\
        WAM_PARAMS=$(pwd)/checkpoints/params.json \\
        uvicorn wam_sidecar:app --host 0.0.0.0 --port 8901

    # Then on the RealGuard backend:
    export REALGUARD_WAM_SIDECAR_URL=http://<sidecar-host>:8901/detect

# Contract

The endpoint accepts ``POST /detect`` with multipart form-data field ``image``
and returns JSON with the schema described in
``imagedetection/views/swarm_wam_expert.py`` docstring.

Set ``WAM_AUTH_TOKEN`` to require ``Authorization: Bearer <token>`` headers.
"""

from __future__ import annotations

import io
import os
import sys
import time
from typing import Any, Dict

try:
    from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover
    sys.stderr.write("fastapi not installed. Run: pip install fastapi uvicorn[standard]\n")
    raise

try:
    import torch
    import torch.nn.functional as F
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.stderr.write("torch/Pillow missing. Install WAM repo's requirements first.\n")
    raise


WAM_REPO_PATH = os.environ.get("WAM_REPO_PATH", "").strip()
WAM_CHECKPOINT = os.environ.get("WAM_CHECKPOINT", "").strip()
WAM_PARAMS = os.environ.get("WAM_PARAMS", "").strip()
WAM_AUTH_TOKEN = os.environ.get("WAM_AUTH_TOKEN", "").strip()
WAM_DEVICE = os.environ.get("WAM_DEVICE", "auto").strip() or "auto"


def _resolve_device() -> str:
    if WAM_DEVICE != "auto":
        return WAM_DEVICE
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_wam_model():
    if not WAM_REPO_PATH or not WAM_CHECKPOINT or not WAM_PARAMS:
        raise RuntimeError(
            "Set WAM_REPO_PATH, WAM_CHECKPOINT and WAM_PARAMS environment variables."
        )
    if WAM_REPO_PATH not in sys.path:
        sys.path.insert(0, WAM_REPO_PATH)
    # Imports must happen after sys.path insert so the WAM source tree resolves.
    from notebooks.inference_utils import (  # type: ignore  # noqa: E402
        load_model_from_checkpoint,
        default_transform,
    )
    from watermark_anything.data.metrics import msg_predict_inference  # type: ignore  # noqa: E402

    model = load_model_from_checkpoint(WAM_PARAMS, WAM_CHECKPOINT)
    device = torch.device(_resolve_device())
    model.to(device).eval()
    return model, default_transform, msg_predict_inference, device


_state: Dict[str, Any] = {}


def _ensure_loaded() -> None:
    if "model" in _state:
        return
    model, transform, msg_predict, device = _load_wam_model()
    _state["model"] = model
    _state["transform"] = transform
    _state["msg_predict"] = msg_predict
    _state["device"] = device


def _check_auth(request: Request) -> None:
    if not WAM_AUTH_TOKEN:
        return
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if header[7:].strip() != WAM_AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="bad token")


app = FastAPI(title="RealGuard WAM sidecar", version="1.0.0")


@app.get("/health")
def health():
    return {"ok": True, "model_loaded": "model" in _state}


@app.post("/detect")
async def detect(
    request: Request,
    image: UploadFile = File(...),
    _: None = Depends(lambda req=None: None),
):
    _check_auth(request)
    _ensure_loaded()
    started = time.time()

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="empty image")

    try:
        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"decode failed: {exc}") from exc

    transform = _state["transform"]
    model = _state["model"]
    msg_predict = _state["msg_predict"]
    device = _state["device"]

    with torch.no_grad():
        tensor = transform(pil).unsqueeze(0).to(device)
        outputs = model.detect(tensor)
        preds = outputs["preds"]                              # [1, 33, 256, 256]
        mask_logits = preds[:, 0, :, :]
        bit_preds = preds[:, 1:, :, :]
        mask_prob = F.sigmoid(mask_logits)

        area_ratio = float((mask_prob > 0.5).float().mean().item())
        watermark_score = float(mask_prob.max().item())
        pred_message = msg_predict(bit_preds, mask_prob).cpu().to(torch.uint8)

    # Pack the 32-bit message into 8 hex chars.
    bits = [int(b) for b in pred_message[0].tolist()]
    packed = 0
    for i, b in enumerate(bits):
        packed |= (b & 1) << i
    message_hex = f"{packed:08x}"

    return JSONResponse({
        "ok": True,
        "watermark_present": watermark_score >= 0.55 and area_ratio >= 0.01,
        "watermark_score": round(watermark_score, 4),
        "watermark_area_ratio": round(area_ratio, 4),
        "predicted_message_hex": message_hex,
        "model_attribution": "",
        "latency_ms": int((time.time() - started) * 1000),
    })
