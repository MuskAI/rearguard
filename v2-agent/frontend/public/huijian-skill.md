# Install the Huijian AI Image Forensics Skill

You are an AI agent helping the user install a trusted Skill. Complete the installation instead of only describing it.

## Goal

Install `huijian-image-forensics`, then explain how the user can ask you to identify whether a local image is real or AI-generated.

## 1. Choose the Skill directory

Use the first path that matches the current Agent:

| Agent | Install directory |
| --- | --- |
| Claude Code | `~/.claude/skills/huijian-image-forensics` |
| Codex | `${CODEX_HOME:-$HOME/.codex}/skills/huijian-image-forensics` |
| Cursor | `~/.cursor/skills/huijian-image-forensics` or project `.cursor/skills/huijian-image-forensics` |
| OpenClaw (龙虾) | `~/.openclaw/skills/huijian-image-forensics` or workspace `skills/huijian-image-forensics` |
| Other compatible Agents | project `.agents/skills/huijian-image-forensics` |

If the Agent has its own documented Skill directory, prefer that directory. Do not overwrite a locally modified installation without first showing the user what will change.

## 2. Download the complete package

Set `TARGET` to the chosen directory, create its subdirectories, and download these files from `https://www.rrreal.cn`:

```text
/skills/huijian-image-forensics/SKILL.md
/skills/huijian-image-forensics/agents/openai.yaml
/skills/huijian-image-forensics/references/api-contract.md
/skills/huijian-image-forensics/scripts/huijian_forensics.py
```

Preserve the directory structure shown above. Make `scripts/huijian_forensics.py` executable when the platform supports file permissions.

## 3. Protect the API key

The Skill reads `HUIJIAN_API_KEY` from the local environment. Never ask the user to paste the full key into chat, never print it, and never write it into the Skill files.

If the variable is missing, direct the user to:

`https://www.rrreal.cn/?developer=1&developerTab=keys`

Then ask them to set it locally:

```bash
export HUIJIAN_API_KEY="rg_sk_..."
export HUIJIAN_API_BASE_URL="https://www.rrreal.cn"
```

## 4. Verify the installation

1. Confirm that `SKILL.md` and `scripts/huijian_forensics.py` exist in the target directory.
2. Run `python3 scripts/huijian_forensics.py --help` from the installed Skill directory.
3. Report the installed path and whether verification passed.
4. Tell the user they can now ask:

> 请鉴别 /absolute/path/photo.jpg 是否为 AI 生成，并用通俗语言列出最重要的证据。

Do not upload a file until the user explicitly provides a path and asks for analysis.
