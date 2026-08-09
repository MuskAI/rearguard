import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const sourceRoot = fileURLToPath(new URL("../../../skills/huijian-image-forensics/", import.meta.url));
const publicRoot = fileURLToPath(new URL("../public/skills/huijian-image-forensics/", import.meta.url));
const files = [
  "SKILL.md",
  "agents/openai.yaml",
  "references/api-contract.md",
  "scripts/huijian_forensics.py",
];

await rm(publicRoot, { recursive: true, force: true });

for (const relativePath of files) {
  const destination = `${publicRoot}${relativePath}`;
  await mkdir(dirname(destination), { recursive: true });
  await copyFile(`${sourceRoot}${relativePath}`, destination);
}

console.log(`Published ${files.length} Huijian Agent Skill files.`);
