# Third-Party Notices

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

- `corzent/yolo11x_watermark_detection`
  - Pinned revision: `796a3b58a1121f20c5976d59314baea3db659a66`
  - Model repository metadata: MIT
  - https://huggingface.co/corzent/yolo11x_watermark_detection

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
