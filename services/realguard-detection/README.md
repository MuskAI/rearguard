# RealGuard GPU inference

This directory tracks the production RealGuard v2 ONNX inference module and
the systemd GPU configuration used on the `10.1.20.66` detection server.

The systemd drop-in exposes only physical GPU 1 to the detection process. It
loads one ONNX Runtime CUDA session during process startup, warms the dynamic
26-view input shape, and keeps one inference in flight to avoid GPU memory
contention. The independent YOLO watermark service remains on physical GPU 0.

## Deployment targets

- `inference_onnx.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/Agent/tools/AIGC_Detection/inference_onnx.py`
- `remote_inference.py` -> `/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/views/remote_inference.py`
- `realguard-detection-gpu.conf` -> `/etc/systemd/system/realguard-detection.service.d/gpu.conf`
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

After installing both files, run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart realguard-detection.service
sudo systemctl status realguard-detection.service --no-pager
```

The startup journal must contain `CUDAExecutionProvider` before the service is
accepted as healthy. With `REALGUARD_V2_REQUIRE_CUDA=1`, the service refuses to
start instead of silently falling back to CPU.
