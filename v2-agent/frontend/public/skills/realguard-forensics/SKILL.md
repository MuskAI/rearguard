---
name: realguard-forensics
description: Use when an AI agent must call RealGuard public CLI/API instructions to detect AIGC, deepfake, tampering, visible watermark, SynthID, or provenance signals from files and summarize verdicts, confidence, evidence, model version, cache version, and report IDs.
---

# RealGuard Forensics

This public skill lets an external agent use RealGuard/Jianzhen forensic detection without relying on a local repository path.

## One-Sentence Handoff

Use `$realguard-forensics`; read `http://124.222.3.205/v2/skills/realguard-forensics/SKILL.md`; run `python3 scripts/realguard_cli.py detect <file> --base-url http://124.222.3.205 --api-prefix /v2-api --token <your-api-key> --pretty` if the RealGuard repository CLI is available, or call `POST http://124.222.3.205/v2-api/detect` with multipart field `file` and header `X-RealGuard-Key: <your-api-key>`; then return a concise verdict with confidence, evidence, model version, cache version, and report ID.

## Public API

Health:

```bash
curl -fsS http://124.222.3.205/v2-api/health
```

Detect:

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

Generate your API Key on `http://124.222.3.205/?page=developer`. Developer keys start with `rg_sk_` and should be passed as `X-RealGuard-Key` or `Authorization: Bearer rg_sk_...`.

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

- Use `agentSummary` first when present.
- Cite `verdict`, `confidence`, `source`, `modelVersion`, `cacheVersion`, `reportId`, and evidence fields.
- Treat results as forensic evidence, not absolute proof.
- If `source` is `mock`, `heuristic`, or another fallback, state that limitation.
- Preserve raw JSON and report IDs for auditability.
