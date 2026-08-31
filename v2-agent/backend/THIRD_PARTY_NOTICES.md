# Third-Party Notices

## Document Image Router

- `wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M`
  - Official implementation: Microsoft Cream / TinyCLIP
  - License: MIT
  - https://github.com/microsoft/Cream/tree/main/TinyCLIP
  - https://huggingface.co/wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M
- ONNX conversion used by the deployment helper:
  - https://huggingface.co/twn39/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX

The router uses the INT8 ONNX model only for broad semantic categories such as
photograph, artwork, logo, icon, chart, diagram, interface screenshot, and
document decoration. It does not use this model to decide whether an image is
real or AI-generated.

## Visible Watermark Detection Assets and Algorithm

The V2 visible watermark detector uses only detection/localization logic and
template assets inspired by these open-source projects:

- `allenk/GeminiWatermarkTool`
  - Copyright (c) 2024 AllenK (Kwyshell)
  - License: MIT
  - https://github.com/allenk/GeminiWatermarkTool

- `wiltodelta/remove-ai-watermarks`
  - Copyright (c) 2025 wiltodelta
  - License: Apache-2.0
  - https://github.com/wiltodelta/remove-ai-watermarks

- Project-supplied binary visible-watermark checkpoint
  - Internal identifier: `huijian/yolo11x_explicit_watermark_binary`
  - Pinned revision: `2026-08-31-f527d8a75420`
  - Checkpoint SHA-256: `f527d8a7542061eb58b0a2953ea86b66b0ecf0b16f3c84d64886e8104c341081`
  - The supplied archive did not contain a license file. Deployment operators
    must document the checkpoint's ownership and redistribution terms before
    distributing the model artifact.

- `ultralytics` runtime
  - Deployed version: `8.4.96`
  - Community license: AGPL-3.0; proprietary deployments may require an
    Ultralytics Enterprise license. Deployment operators must confirm the
    license appropriate for their distribution and service model.
  - https://www.ultralytics.com/license

Only watermark presence, location, confidence, and evidence metadata are used.
No reverse alpha blending, inpainting, or watermark-removal output is included.

## Experimental SynthID Spectral Detection

- `aloshdenny/reverse-SynthID`
  - Pinned deployment revision: `9607671`
  - License: reverse-SynthID Research License v1.0 (non-commercial use only;
    public attribution required; commercial use requires a separate license)
  - https://github.com/aloshdenny/reverse-SynthID

Attribution: **reverse-SynthID by Alosh Denny**.

慧鉴 AI imports only the community project's spectral detection path and V4
codebook. It does not expose or invoke watermark removal, dissolution, or bypass
features. Results are explicitly presented as experimental community evidence,
not as Google or Google DeepMind official SynthID verification.
