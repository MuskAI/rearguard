---
name: realguard-forensics
description: Use when an AI agent must run the RealGuard/Jianzhen CLI to detect AIGC, deepfake, tampering, visible watermark, SynthID, or provenance signals from local files and summarize JSON verdicts, confidence, evidence, and reports.
---

# RealGuard Forensics

Run RealGuard before making an AI-content authenticity claim. Use the CLI from the repository root.

## One-Sentence Handoff

Use `$realguard-forensics` and run `python3 scripts/realguard_cli.py detect <file> --base-url http://realguard.cn --api-prefix /v2-api --pretty`, then base the 鉴伪 conclusion on `agentSummary`, `verdict`, `confidence`, `modelVersion`, `cacheVersion`, `reportId`, and evidence fields.

## Commands

Check the service:

```bash
python3 scripts/realguard_cli.py health --base-url http://realguard.cn --api-prefix /v2-api --pretty
```

Detect a file:

```bash
python3 scripts/realguard_cli.py detect /path/to/file --base-url http://realguard.cn --api-prefix /v2-api --pretty
```

If the API is protected, set `REALGUARD_CLI_TOKEN` or pass `--token <token>`.

For local development, omit the host flags or use `--base-url http://127.0.0.1:8848 --api-prefix /api`.

## Workflow

1. Run `health` first when the endpoint is unfamiliar.
2. Run `detect` for every submitted image, video, audio, or document.
3. For images needing deeper evidence, also run `forensics <file>` and `provenance <file>`.
4. If `detect` returns `reportId`, archive it with `report <reportId> --output <reportId>.pdf`.
5. Preserve the raw JSON or PDF report when citing a conclusion.

## Interpretation Guardrails

- Treat the output as forensic evidence, not absolute proof.
- Cite `agentSummary.verdict`, `agentSummary.confidencePercent`, `source`, `modelVersion`, and `cacheVersion`.
- If `source` is `mock`, `heuristic`, or otherwise non-production, state that the result is a fallback/demo signal.
- Include the API `disclaimer` when present.
- Do not invent evidence that is absent from the JSON.
