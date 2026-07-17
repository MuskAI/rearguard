# RealGuard GPU inference

This directory tracks the production RealGuard v2 ONNX inference module and
the systemd GPU configuration used on the `10.1.20.66` detection server.

The systemd drop-in exposes only physical GPU 1 to the detection process. It
loads one ONNX Runtime CUDA session during process startup, warms the dynamic
26-view input shape, and allows up to two in-flight requests on physical GPU 1.
The independent YOLO watermark service remains on physical GPU 0, so visible
watermark localization does not consume the main model's GPU queue.

## Deployment targets

- `inference_onnx.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/Agent/tools/AIGC_Detection/inference_onnx.py`
- `remote_inference.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/views/remote_inference.py`
- `realguard-detection-gpu.conf` -> `/etc/systemd/system/realguard-detection.service.d/gpu.conf`
- `realguard-detection-shared-precheck.conf` -> `/etc/systemd/system/realguard-detection.service.d/shared-precheck.conf`
- `realguard-web-tunnel.service` -> `/etc/systemd/system/realguard-web-tunnel.service`
- `public-detector-remote.conf` -> `/etc/systemd/system/realguard-detector-backend.service.d/remote.conf` on the public server

Register `model_inference_blueprint` in the detection server app factory. The
public detector calls `/internal/model/predict` through a reverse SSH tunnel,
while user history and metadata remain owned by the public application server.

The untracked `/etc/realguard/model-inference.env` file must contain the same
`REALGUARD_MODEL_INTERNAL_TOKEN` on both servers. On the public server it also
defines `REALGUARD_REMOTE_INFERENCE_URL` as the tunnel-local internal endpoint.

The public detector still performs local metadata extraction before calling
the GPU model and therefore requires ExifTool:

```bash
sudo apt-get install libimage-exiftool-perl
```

## Shared-upload precheck

`remote_inference.py` starts the local visible-watermark precheck while the
resident GPU model processes the same request body. The compact precheck result
is returned as `visibleWatermarkPrecheck`, so the public Swarm orchestrator does
not upload the image to the private network a second time.

The watermark service environment file is loaded by
`realguard-detection-shared-precheck.conf`. Keep the precheck URL loopback-only:

```text
REALGUARD_MODEL_VISIBLE_PRECHECK_URL=http://127.0.0.1:5066/v1/precheck
REALGUARD_MODEL_VISIBLE_PRECHECK_TIMEOUT=12
REALGUARD_MODEL_VISIBLE_PRECHECK_WORKERS=4
```

The precheck service runs provenance reporting, known-platform matching, and
YOLO localization concurrently. Its production unit uses four request threads;
the YOLO unit uses two single-threaded worker processes on physical GPU 0.

On the public server, the persisted `v1-legacy-tunnel` model route must use
`http://127.0.0.1:15001/image` with health endpoint
`http://127.0.0.1:15001/health`. A remote endpoint saved in
`model_registry.json` overrides the environment default and bypasses shared
evidence reuse.

Swarm starts the V2 network expert after an upload-size-aware delay to avoid
making two large uploads compete for the same private-network link. Defaults:

```text
REALGUARD_SWARM_V2_STAGGER_BYTES_PER_SECOND=800000
REALGUARD_SWARM_V2_MAX_STAGGER_SECONDS=8
```

Set either value only after an end-to-end benchmark with production-size
images. Starting both uploads immediately increased the main model latency in
the current deployment.

After installing both files, run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart realguard-detection.service
sudo systemctl status realguard-detection.service --no-pager
```

The startup journal must contain `CUDAExecutionProvider` before the service is
accepted as healthy. With `REALGUARD_V2_REQUIRE_CUDA=1`, the service refuses to
start instead of silently falling back to CPU.
