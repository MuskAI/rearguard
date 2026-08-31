# YOLO11x Visible Watermark Service

Detection-only GPU service for the project-supplied binary visible-watermark
checkpoint `huijian/yolo11x_explicit_watermark_binary`. It is
bound to `127.0.0.1:5067` on server 66 and is consumed by the provenance
precheck adapter; the model port is not exposed publicly.

## Pinned Artifact

- Model identifier: `huijian/yolo11x_explicit_watermark_binary`
- Revision: `2026-08-31-f527d8a75420`
- Checkpoint SHA-256: `f527d8a7542061eb58b0a2953ea86b66b0ecf0b16f3c84d64886e8104c341081`
- Source archive SHA-256: `b425c17f9eee729af5b03c26924599b22c41bb0a37f19671c27fdd85688a04d4`
- Classes: `{0: watermark}`
- Runtime: `ultralytics==8.4.96`

The application verifies the checkpoint digest before loading it and disables
Ultralytics auto-install and network-dependent behavior.

## Configuration

```dotenv
YOLO_WATERMARK_TOKEN=replace-with-a-long-random-secret
YOLO_WATERMARK_MODEL=/home/ymk/services/yolo-watermark/models/best.pt
YOLO_WATERMARK_MODEL_NAME=huijian/yolo11x_explicit_watermark_binary
YOLO_WATERMARK_REVISION=2026-08-31-f527d8a75420
YOLO_WATERMARK_MODEL_SHA256=f527d8a7542061eb58b0a2953ea86b66b0ecf0b16f3c84d64886e8104c341081
YOLO_WATERMARK_DEVICE=0
YOLO_WATERMARK_REQUIRE_CUDA=true
YOLO_WATERMARK_IMAGE_SIZE=512
YOLO_WATERMARK_CONFIDENCE=0.25
YOLO_WATERMARK_IOU=0.45
YOLO_WATERMARK_MAX_BYTES=31457280
YOLO_WATERMARK_WARMUP=true
```

Production startup fails closed when CUDA is unavailable. Health responses
include the active device, GPU name, pinned revision, checkpoint SHA-256,
process ID, load count, load time, and warmup state; the upstream precheck
rejects a mismatched or CPU-backed runtime. Production uses one Gunicorn worker
so the checkpoint has one resident GPU copy. Two request threads accept work,
while an inference lock serializes access to the single CUDA model instance.

The endpoint returns normalized bounding boxes so the browser can overlay
them on images of any display size. Generic visible watermarks and logos are
supplementary evidence only and never change the AI-authenticity score.

Review `v2-agent/backend/THIRD_PARTY_NOTICES.md` before redistribution or
commercial deployment. Ultralytics publishes separate AGPL-3.0 community and
Enterprise licensing paths.
