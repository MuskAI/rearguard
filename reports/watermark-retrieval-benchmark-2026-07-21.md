# Explicit Watermark Retrieval Benchmark

Date: 2026-07-21

Service: `http://10.1.20.66:5070/api/analyze`

Backend: `faiss_clip` with `bright_overlay_v1`

## Index

- 20 reference crops: Google Gemini 11, Samsung Galaxy AI 4, Doubao 3, Jimeng 2.
- 512-dimensional normalized CLIP image embeddings in FAISS `IndexFlatIP`.
- 69 negative corner crops used for threshold calibration.
- Acceptance requires a per-platform similarity threshold, a minimum gap over
  the second platform, and a compatible watermark position.
- OCR can resolve an ambiguous first/second-platform ordering only when the OCR
  platform's own retrieval score and position pass calibration.

## Before and after

| Metric | Template baseline | FAISS retrieval | Change |
|---|---:|---:|---:|
| Positive recall | 72% | 96% | +24 pp |
| Platform accuracy | 68% | 92% | +24 pp |
| False-positive rate | 0/33 | 0/33 | unchanged |
| Mean service latency | 3,933 ms | 4,339 ms | +406 ms |
| P95 service latency | 9,286 ms | 9,306 ms | +20 ms |

The retrieval build recovered all six Samsung captures that the template
baseline missed. The only remaining positive miss is the white-background
Gemini capture, where the white translucent mark is not visibly separable from
the background. One real Gemini image is detected as an AI watermark through
registry evidence but remains unattributed by retrieval under the conservative
Gemini threshold.

## Calibration

| Platform | References | Threshold | Min margin | Positive LOO P10 | Negative P99.5 |
|---|---:|---:|---:|---:|---:|
| Google Gemini | 11 | 0.9735 | 0.0648 | 0.9412 | 0.9435 |
| Samsung Galaxy AI | 4 | 0.8296 | 0.0890 | 0.9919 | 0.8046 |
| Jimeng AI | 2 | 0.8447 | 0.0240 | 0.9981 | 0.8197 |
| Doubao | 3 | 0.8554 | 0.0150 | 0.9478 | 0.8304 |

Gemini has overlapping positive and negative calibration bands. Its high
threshold is deliberate: an unseen ambiguous sparkle remains `unknown` unless
registry or other provenance evidence corroborates it.

## Reproducibility

- Final batch records: `watermark-benchmark-2026-07-21-retrieval-final.json`
- Index metadata: `../services/watermark-ensemble/retrieval/metadata.json`
- Calibration: `../services/watermark-ensemble/retrieval/calibration.json`
- Builder: `../services/watermark-ensemble/build_retrieval_index.py`
- Batch runner: `../scripts/benchmark_watermark_lab.py`
