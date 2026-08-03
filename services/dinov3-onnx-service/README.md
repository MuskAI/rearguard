# DINOv3 ViT-7B candidate service

This adapter exposes the packaged DINOv3 ONNX detector through the legacy
RealGuard model contract without copying or modifying the 13 GB model bundle.

## Server layout

- Model package: `/home/ymk/model_registry/dinov3_vit7b16_linear_onnx_fp16`
- Service source: `/home/ymk/services/dinov3-onnx-service`
- Local endpoint: `http://127.0.0.1:5071`
- Public-server tunnel endpoint: `http://127.0.0.1:15002`
- Admin model id: `dinov3-vit7b16-linear-fp16`

The two Titan Xp cards each have 12 GB VRAM. The service splits the 40-block
FP16 graph after block 20 and runs each half in an isolated process on a
different GPU. Isolation matters: repeatedly switching two CUDA devices from
one ONNX Runtime process is unstable on this Pascal deployment.

| Stage | Blocks | Default GPU | Added VRAM |
| --- | --- | --- | --- |
| `stage1.onnx` | 1-20 | GPU 1 | about 6.56 GB |
| `stage2.onnx` | 21-40 + head | GPU 0 | about 6.56 GB |

The intermediate feature copied between workers is about 1.57 MB at batch
size 1. A 12-run smoke benchmark reached a 0.332 second median after warm-up,
compared with 4.50 seconds for the complete CPU graph. The service remains
single-flight because both cards also host the production detector and
watermark models.

Create the split artifacts on the large data volume:

```bash
python split_model.py \
  /home/ymk/model_registry/dinov3_vit7b16_linear_onnx_fp16/model/dinov3_vit7b16_linear_fp16.onnx \
  /mnt/sda1/ymk/dinov3_split_fp16
```

Validate the isolated-worker path without starting the HTTP service:

```bash
python split_process_smoke.py --iterations 12 \
  --image /path/to/a/test-image.jpg
```

Set `DINOV3_PROVIDER=cpu` and restore the original memory limits in the unit
file to roll back to the unsplit CPU graph.

Dynamic INT8 quantization was tested on the FP16 export. Although each half
shrunk from 6.3 GB to 3.2 GB, ONNX Runtime rejected `DynamicQuantizeLinear`
with FP16 activations before provider assignment. It is not a usable GPU INT8
artifact. TensorRT INT8 is also not used because current TensorRT releases do
not support the Titan Xp's SM 6.1 architecture.

The service returns a binary model label and raw fake score, but intentionally
marks the decision as `review_only` until an independently signed production
calibration manifest is available. Watermark, metadata, and provenance policy
remain owned by the public RealGuard application.
