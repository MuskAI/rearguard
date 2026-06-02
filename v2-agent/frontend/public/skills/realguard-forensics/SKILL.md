---
name: realguard-forensics
description: Use when an AI agent must call RealGuard public CLI/API instructions to detect AIGC, deepfake, tampering, visible watermark, SynthID, or provenance signals from files and summarize verdicts, confidence, evidence, model version, cache version, and report IDs.
---

# RealGuard Forensics

This public skill lets an external agent use RealGuard/Jianzhen forensic detection without relying on a local repository path.

## One-Sentence Handoff

Use `$realguard-forensics`; read `http://124.222.3.205/v2/skills/realguard-forensics/SKILL.md`; prefer V2 by calling `POST http://124.222.3.205/v2-api/detect` with multipart field `file` and `X-RealGuard-Key: <your-api-key>` for multimodal forensic output and `tokenUsage`; alternatively use V1 by calling `POST http://124.222.3.205/api/developer/v1/detect` with multipart field `file` for the legacy image model; then return a concise verdict with confidence, evidence, model/source, usage or call-count note, and report/item ID.

## Public API

Generate your API Key on `http://124.222.3.205/?page=developer`. Developer keys start with `rg_sk_` and should be passed as `X-RealGuard-Key` or `Authorization: Bearer rg_sk_...`.

### V2 Multimodal API

Health:

```bash
curl -fsS http://124.222.3.205/v2-api/health
```

Detect with V2:

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/detect \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/file"
```

Optional file type override:

```bash
curl -fsS -X POST http://124.222.3.205/v2-api/detect \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/file" \
  -F "fileType=image"
```

### V1 Image API

Detect with V1:

```bash
curl -fsS -X POST http://124.222.3.205/api/developer/v1/detect \
  -H "X-RealGuard-Key: <your-api-key>" \
  -F "file=@/path/to/image.png"
```

Use V1 when the caller explicitly needs the legacy RealGuard image model or must compare with old image-only results. V1 records API-key call count in the developer platform but does not return `tokenUsage`.

## Repository CLI

When the RealGuard repository is available locally:

```bash
python3 scripts/realguard_cli.py detect /path/to/file --base-url http://124.222.3.205 --api-prefix /v2-api --token <your-api-key> --pretty
```

For image-only evidence:

```bash
python3 scripts/realguard_cli.py forensics /path/to/image --base-url http://124.222.3.205 --api-prefix /v2-api --token <your-api-key> --pretty
python3 scripts/realguard_cli.py provenance /path/to/image --base-url http://124.222.3.205 --api-prefix /v2-api --token <your-api-key> --pretty
```

## Interpretation

- For V2, use `agentSummary` first when present.
- For V2, cite `verdict`, `confidence`, `source`, `modelVersion`, `cacheVersion`, `reportId`, `tokenUsage`, and evidence fields.
- For V1, cite `result.final_label`, `result.probability`, `result.confidence`, `result.visual_issues`, and `result.itemid`.
- Treat `tokenUsage.totalTokens` as the V2 model-call cost signal; cache hits may report `0` tokens.
- Treat developer-platform call count as the cross-pipeline usage signal for both V1 and V2.
- Treat results as forensic evidence, not absolute proof.
- If `source` is `mock`, `heuristic`, or another fallback, state that limitation.
- Preserve raw JSON and report IDs for auditability.
