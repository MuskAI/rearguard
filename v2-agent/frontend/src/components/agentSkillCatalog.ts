import claudeCodeLogo from "@lobehub/icons-static-svg/icons/claudecode-color.svg";
import clineLogo from "@lobehub/icons-static-svg/icons/cline.svg";
import codeBuddyLogo from "@lobehub/icons-static-svg/icons/codebuddy-color.svg";
import codexLogo from "@lobehub/icons-static-svg/icons/codex-color.svg";
import cursorLogo from "@lobehub/icons-static-svg/icons/cursor.svg";
import kimiLogo from "@lobehub/icons-static-svg/icons/kimi-color.svg";
import openClawLogo from "@lobehub/icons-static-svg/icons/openclaw-color.svg";
import qwenLogo from "@lobehub/icons-static-svg/icons/qwen-color.svg";
import traeLogo from "@lobehub/icons-static-svg/icons/trae-color.svg";
import windsurfLogo from "@lobehub/icons-static-svg/icons/windsurf.svg";

export const AGENT_CLIENTS = [
  { name: "Claude Code", detail: "原生 Agent Skills", logo: claudeCodeLogo },
  { name: "Codex", detail: "个人与项目 Skill", logo: codexLogo },
  { name: "Cursor", detail: "编辑器与 CLI", logo: cursorLogo },
  { name: "OpenClaw", detail: "龙虾 Agent", logo: openClawLogo },
  { name: "TRAE", detail: "国内开发 Agent", logo: traeLogo },
  { name: "Kimi", detail: "终端与文件工作流", logo: kimiLogo },
  { name: "Qwen Code", detail: "命令行 Agent", logo: qwenLogo },
  { name: "CodeBuddy", detail: "开发 Agent", logo: codeBuddyLogo },
  { name: "Windsurf", detail: "编辑器 Agent", logo: windsurfLogo },
  { name: "Cline", detail: "VS Code Agent", logo: clineLogo },
] as const;

export const FEATURED_AGENT_CLIENTS = AGENT_CLIENTS.slice(0, 5);

export function buildAgentSkillInstallPrompt(origin: string) {
  return `请读取 ${origin}/huijian-skill.md，按照说明安装「慧鉴AI 图像鉴伪」Skill；安装完成后告诉我如何配置 HUIJIAN_API_KEY，并用一个本地图片路径完成验证。`;
}
