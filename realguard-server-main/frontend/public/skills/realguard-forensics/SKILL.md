---
name: realguard-forensics
description: Use when an AI agent must call RealGuard public CLI/API instructions to detect AIGC, deepfake, tampering, visible watermark, SynthID, or provenance signals from files and summarize verdicts, confidence, evidence, model version, cache version, and report IDs.
---

# RealGuard Forensics

This public skill lets an external agent use RealGuard/Jianzhen forensic detection without relying on a local repository path.

## One-Sentence Handoff

Use `$realguard-forensics`; read `http://realguard.cn/skills/realguard-forensics/SKILL.md`; run `python3 scripts/realguard_cli.py detect <file> --base-url http://realguard.cn --api-prefix /v2-api --pretty` if the RealGuard repository CLI is available, or call `POST http://realguard.cn/v2-api/detect` with multipart field `file`; then return a concise verdict with confidence, evidence, model version, cache version, and report ID.

## Public API

Health:

```bash
curl -fsS http://realguard.cn/v2-api/health
```

Detect:

```bash
curl -fsS -X POST http://realguard.cn/v2-api/detect \
  -F "file=@/path/to/file"
```

Optional file type override:

```bash
curl -fsS -X POST http://realguard.cn/v2-api/detect \
  -F "file=@/path/to/file" \
  -F "fileType=image"
```

If access protection is enabled, pass `X-Jianzhen-Token: <token>` or `Authorization: Bearer <token>`.

## Repository CLI

When the RealGuard repository is available locally:

```bash
python3 scripts/realguard_cli.py detect /path/to/file --base-url http://realguard.cn --api-prefix /v2-api --pretty
```

For image-only evidence:

```bash
python3 scripts/realguard_cli.py forensics /path/to/image --base-url http://realguard.cn --api-prefix /v2-api --pretty
python3 scripts/realguard_cli.py provenance /path/to/image --base-url http://realguard.cn --api-prefix /v2-api --pretty
```

## Interpretation

- Use `agentSummary` first when present.
- Cite `verdict`, `confidence`, `source`, `modelVersion`, `cacheVersion`, `reportId`, and evidence fields.
- Treat results as forensic evidence, not absolute proof.
- If `source` is `mock`, `heuristic`, or another fallback, state that limitation.
- Preserve raw JSON and report IDs for auditability.
