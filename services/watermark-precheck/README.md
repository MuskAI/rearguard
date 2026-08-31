# Explicit Watermark Precheck

Detection-only service deployed on `10.1.20.66`. The active watermark path is
model-direct: it calls the pinned `huijian/yolo11x_explicit_watermark_binary`
service and returns its boxes and confidence scores without OCR, logo/template
retrieval, a platform registry, or rule fusion.

No watermark removal, inpainting, metadata stripping, or diffusion code is
used by this service.

## Request Path

1. Decode and normalize the uploaded image for the detector.
2. Call the resident GPU model at `127.0.0.1:5067`.
3. Return each model box in `display_normalized_v1` coordinates with the exact
   model confidence.
4. Mark a localized hit as decisive only when its confidence is at least
   `YOLO_WATERMARK_DIRECT_DECISIVE_CONFIDENCE` (default `0.80`). Lower scores
   remain visible in the report but do not override the main authenticity
   model.

Metadata and C2PA parsing remain independent source-evidence features in the
public backend. They are not part of the explicit-watermark model path.

The precheck is fail-open for availability: a timeout or model outage records
the failure and lets the main authenticity model continue. It never fabricates
a fallback watermark result.

## Response Contract

`POST /v1/precheck` returns:

- `mode=model_direct` and `resultSource=model`;
- `visibleHits` containing only direct model detections;
- `explicitWatermark.confidence` equal to the highest model score;
- one detector engine: `explicit_watermark_model_direct`;
- `pipelineTrace` with only `decode`, `yolo`, and `verdict` stages.

The old registry, OCR, retrieval, and fusion fields are intentionally absent
from new responses. The public backend keeps a legacy reader so previously
stored reports remain viewable.

The service binds to `127.0.0.1:5066`. Production reaches it through a
loopback-only reverse SSH tunnel at `127.0.0.1:15066` on the public web server.

## Configuration

```dotenv
WATERMARK_PRECHECK_TOKEN=replace-with-a-long-random-secret
WATERMARK_PRECHECK_MAX_BYTES=31457280
YOLO_WATERMARK_URL=http://127.0.0.1:5067/v1/detect
YOLO_WATERMARK_HEALTH_URL=http://127.0.0.1:5067/health
YOLO_WATERMARK_TOKEN=replace-with-the-model-service-token
YOLO_WATERMARK_TIMEOUT_SECONDS=20
YOLO_WATERMARK_REQUIRE_CUDA=1
YOLO_WATERMARK_DIRECT_DECISIVE_CONFIDENCE=0.80
```

Public backend transport settings:

```dotenv
JIANZHEN_PROVENANCE_PRECHECK_URL=http://127.0.0.1:15066
JIANZHEN_PROVENANCE_PRECHECK_TOKEN=replace-with-the-shared-precheck-token
JIANZHEN_PROVENANCE_PRECHECK_TIMEOUT=8
JIANZHEN_PROVENANCE_PRECHECK_ORIGINAL_TIMEOUT=20
JIANZHEN_PROVENANCE_PRECHECK_DIRECT_UPLOAD_MAX_BYTES=1572864
JIANZHEN_PROVENANCE_PRECHECK_SCAN_MAX_SIDE=1536
JIANZHEN_PROVENANCE_PRECHECK_SCAN_QUALITY=94
```

## Local Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
WATERMARK_PRECHECK_TOKEN=dev-token .venv/bin/flask --app yolo_adapter run --port 5066
```
