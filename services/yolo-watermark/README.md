# YOLO11x Visible Watermark Service

Detection-only GPU service for `corzent/yolo11x_watermark_detection`. It is
bound to `127.0.0.1:5067` on server 66 and is consumed by the provenance
precheck adapter; the model port is not exposed publicly.

## Pinned Artifact

- Repository: `https://huggingface.co/corzent/yolo11x_watermark_detection`
- Revision: `796a3b58a1121f20c5976d59314baea3db659a66`
- Checkpoint SHA-256: `6ac71b6ab8db27ec7928b5176e60a359c65e1579a5c1d58cf2f98df30cf3085e`
- Runtime: `ultralytics==8.4.96`

The application verifies the checkpoint digest before loading it and disables
Ultralytics auto-install and network-dependent behavior.

## Configuration

```dotenv
YOLO_WATERMARK_TOKEN=replace-with-a-long-random-secret
YOLO_WATERMARK_MODEL=/home/ymk/services/yolo-watermark/models/best.pt
YOLO_WATERMARK_REVISION=796a3b58a1121f20c5976d59314baea3db659a66
YOLO_WATERMARK_MODEL_SHA256=6ac71b6ab8db27ec7928b5176e60a359c65e1579a5c1d58cf2f98df30cf3085e
YOLO_WATERMARK_DEVICE=0
YOLO_WATERMARK_REQUIRE_CUDA=true
YOLO_WATERMARK_IMAGE_SIZE=1280
YOLO_WATERMARK_CONFIDENCE=0.35
YOLO_WATERMARK_IOU=0.50
YOLO_WATERMARK_MAX_BYTES=31457280
YOLO_WATERMARK_WARMUP=true
```

Production startup fails closed when CUDA is unavailable. Health responses
include the active device, GPU name, pinned revision, and checkpoint SHA-256;
the upstream precheck rejects a mismatched or CPU-backed runtime.

The endpoint returns normalized bounding boxes so the browser can overlay
them on images of any display size. Generic visible watermarks and logos are
supplementary evidence only and never change the AI-authenticity score.

Review `v2-agent/backend/THIRD_PARTY_NOTICES.md` before redistribution or
commercial deployment. Ultralytics publishes separate AGPL-3.0 community and
Enterprise licensing paths.
