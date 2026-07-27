import fs from "node:fs/promises";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(process.env.AIGC_REPO_ROOT || path.join(SCRIPT_DIR, ".."));
const NOTES_PATH = path.resolve(
  process.env.AIGC_PAPER_NOTES || path.join(ROOT, "reports/ppt-work/all_paper_notes.json"),
);
const MANIFEST_PATH = path.resolve(
  process.env.AIGC_PAPER_MANIFEST || path.join(ROOT, "reports/ppt-work/paper_manifest.json"),
);
const OUT_PATH = path.resolve(
  process.env.AIGC_READING_REPORT || path.join(ROOT, "reports/aigc-image-detection-reading-report.md"),
);

const notes = JSON.parse(readFileSync(NOTES_PATH, "utf8")).papers;
const manifest = JSON.parse(readFileSync(MANIFEST_PATH, "utf8")).items;
const byIndex = new Map(notes.map((p) => [p.index, p]));
const manifestByIndex = new Map(manifest.map((p) => [p.index, p]));

const groups = [
  {
    id: "A",
    title: "低层线索与生成过程证据",
    thesis: "这一组把检测证据从语义内容转向生成/成像过程留下的微弱痕迹：相位谱、RAW-RGB、重建差分、扩散时间步、低位平面和像素映射。共同目标是绕开语义捷径，让检测器看见更接近取证本质的信号。",
    indexes: [1, 4, 7, 9, 13, 18, 24, 34, 36, 38, 43, 45, 46, 47],
  },
  {
    id: "B",
    title: "泛化、适配与开放世界检测",
    thesis: "这一组关注检测器如何面对未知生成器、闭源商业模型和持续变化的数据分布。核心矛盾不是训练数据越多越好，而是怎样保留可迁移先验、避免生成器冲突，并在新模型出现时快速适配。",
    indexes: [3, 15, 16, 19, 25, 27, 32, 37, 40, 42, 48, 49],
  },
  {
    id: "C",
    title: "真实世界鲁棒性、数据集与评测协议",
    thesis: "这一组把问题从 clean benchmark 推到真实平台：压缩、截图、平台转码、样本来源错配、wild fake 收集和解释性 benchmark。它们提醒我们，检测器失败常常不是模型不够大，而是训练协议和真实传播链路不匹配。",
    indexes: [28, 29, 30, 31, 33],
  },
  {
    id: "D",
    title: "解释、定位、归因与 MLLM 推理",
    thesis: "这一组把检测从一个真假分数扩展为可审查的取证流程：定位可疑区域、检索源模型、构造 forensic concept、输出自然语言解释、用 MLLM 做 grounded reasoning。这里的关键不是让模型会说话，而是让解释绑定证据。",
    indexes: [2, 5, 8, 20, 21, 22, 23, 26, 35, 39],
  },
  {
    id: "E",
    title: "多 cue、多专家与统一框架",
    thesis: "这一组认为单一证据源不足以覆盖所有 AIGC 场景，于是组合 RGB、NPR、频域、色度、质量先验、生成签名和 foundation model 表征。难点不在拼更多特征，而在让不同专家互补、对齐且不过拟合。",
    indexes: [6, 10, 11, 12, 14, 17, 41, 44],
  },
];

const missingPdf = new Set([19, 20, 33]);

function clean(s = "") {
  return String(s).replace(/\s+/g, " ").trim();
}

function mdEscape(s = "") {
  return clean(s).replace(/\|/g, "\\|");
}

function confidence(p) {
  if (p.source_confidence) return p.source_confidence;
  const item = manifestByIndex.get(p.index);
  return item?.pdfPath ? "full_pdf" : "abstract_or_metadata_only";
}

function paperLine(idx) {
  const p = byIndex.get(idx);
  return `${idx}. ${p.title}`;
}

function paperCard(p) {
  const status = missingPdf.has(p.index) ? "摘要/元信息，待原文核验" : confidence(p);
  return [
    `### ${p.index}. ${p.title}`,
    "",
    `- **会议/来源**：${p.venue || "待补充"}`,
    `- **阅读可信度**：${status}`,
    `- **一句话结论**：${clean(p.one_sentence || "待补充。")}`,
    `- **Motivation**：${clean(p.motivation || "待补充。")}`,
    `- **Problem**：${clean(p.problem || "待补充。")}`,
    `- **Method**：${clean(p.method || "待补充。")}`,
    `- **Experiments**：${clean(p.experiments || "待补充。")}`,
    `- **Strengths**：${clean(p.strengths || "待补充。")}`,
    `- **Limitations**：${clean(p.limitations || "待补充。")}`,
    `- **阅读/汇报角度**：${clean(p.ppt_angle || p.slide_suggestion || "待补充。")}`,
    "",
  ].join("\n");
}

function compactTable(group) {
  const rows = [
    "| 编号 | 论文 | 关键点 |",
    "| --- | --- | --- |",
  ];
  for (const idx of group.indexes) {
    const p = byIndex.get(idx);
    rows.push(`| ${idx} | ${mdEscape(p.title)} | ${mdEscape(p.one_sentence || "")} |`);
  }
  return rows.join("\n");
}

const allTitles = notes.map((p) => `- ${p.index}. ${p.title}（${p.venue || "source unknown"}${missingPdf.has(p.index) ? "，摘要/待核验" : ""}）`).join("\n");

const report = [
  "# AIGC 图像检测论文阅读报告",
  "",
  "生成日期：2026-06-20",
  "",
  "## 0. 阅读范围与可信度",
  "",
  "本报告基于当前已整理的 49 篇 AIGC 图像检测相关论文笔记生成。46 篇有本地 PDF 文本抽取，3 篇仅有摘要或元信息，需要后续原文核验：",
  "",
  "- 19. Fleet: Few-Shots Lead Effective AIGI Detection",
  "- 20. Forensic Prompting with Dual-Action Policy Optimization for Vision-Language Forgery Detection and Localization",
  "- 33. Combating Dataset Misalignment for Robust AI-Generated Image Detection in the Real World",
  "",
  "注意：本报告定位为“快速阅读 + 汇报准备”的研究笔记。多数论文的动机、方法和核心实验来自本地 PDF 文本抽取后的结构化整理；若要在正式论文/开题材料中引用精确表格数字，建议回到原 PDF 的实验表格二次核验。",
  "",
  "## 1. 总体判断",
  "",
  "这批论文显示，AIGC 图像检测正在从“训练一个二分类器”转向“开放世界取证系统”。新的检测器不仅要回答真假，还要处理压缩与平台传播、未知生成器、少样本适配、区域定位、源模型归因、解释可信度和持续数据更新。",
  "",
  "可以把主线概括为四个关键词：",
  "",
  "1. **证据下沉**：从图像语义转向相位、RAW-RGB、低位平面、NPR、扩散路径、重建残差等低层或过程证据。",
  "2. **泛化适配**：从静态跨生成器泛化转向新生成器少样本适配、主动难例发现、real-only 表征和持续学习。",
  "3. **真实部署**：从 clean benchmark 转向压缩、截图、平台转码、wild fake 和数据错配审计。",
  "4. **可审查取证**：从单一分数转向定位、解释、归因、概念 codebook 和 MLLM reasoning。",
  "",
  "## 2. 技术路线总览",
  "",
  ...groups.flatMap((group) => [
    `### ${group.id}. ${group.title}`,
    "",
    group.thesis,
    "",
    compactTable(group),
    "",
  ]),
  "## 3. 横向结论",
  "",
  "### 3.1 低层证据仍然是通用检测的根",
  "",
  "相位谱、RAW-RGB、NPR、低位平面和重建路径的共同价值，是把检测器从内容语义中拉出来。语义模型越强，越容易把类别、风格、构图当成真假 shortcut；低层证据则更接近生成器或成像链路本身。不过这类方法也最容易受到后处理、平台转码和自适应攻击影响，因此需要和鲁棒性校准结合。",
  "",
  "### 3.2 泛化不能只靠堆数据",
  "",
  "GAPL、DGS-Net、LTD、HSIC、DNA、Fleet 等论文从不同角度说明：更多生成器训练样本并不必然带来更好泛化。多生成器之间会冲突，CLIP 微调会遗忘，数据分布会错配，新商业模型会快速改变伪迹。未来检测器更像安全系统，需要快速打补丁，而不是一次训练永久有效。",
  "",
  "### 3.3 数据集和协议本身已经成为方法的一部分",
  "",
  "MIRAGE、WildFC、X-AIGD、Aligned Datasets、Combating Dataset Misalignment 等工作说明，评测协议会深刻改变结论。真实/生成图若在来源、分辨率、压缩、语义类别上不对齐，检测器可能学到非伪迹 shortcut。阅读这类论文时，不能只看模型结构，要先看数据如何采集、对齐、过滤和切分。",
  "",
  "### 3.4 MLLM 的价值在解释，但风险也在解释",
  "",
  "Locate-Then-Examine、OmniVL-Guard、Veritas、TranX-Adapter、ForensicConcept 等论文都在把检测变成可解释流程。关键问题是：解释是否真的由取证证据支撑？如果 MLLM 只是把分类结果包装成自然语言，它的解释价值很有限；如果解释能绑定 bbox、mask、concept、retrieval neighbor 或低层证据，它才可能成为可审查系统的一部分。",
  "",
  "### 3.5 最现实的系统路线是组合式",
  "",
  "单一路线很难覆盖所有部署需求。低层证据适合抗语义捷径，CLIP/VLM 适合语义泛化，wild 数据适合真实平台，MLLM 适合解释和定位，可信度校准适合拒判与路由。最有前景的系统是“低层证据 + 泛化适配 + 可信度校准 + MLLM 解释”的组合。",
  "",
  "## 4. 逐篇阅读笔记",
  "",
  ...groups.flatMap((group) => [
    `## ${group.id}. ${group.title}`,
    "",
    group.thesis,
    "",
    ...group.indexes.map((idx) => paperCard(byIndex.get(idx))),
  ]),
  "## 5. 论文清单",
  "",
  allTitles,
  "",
  "## 6. 建议后续精读顺序",
  "",
  "如果时间有限，建议先精读以下 12 篇，因为它们分别代表了当前研究路线的关键转折点：",
  "",
  "1. Detecting Compressed AI-Generated Images via Phase Spectrum Robustness：压缩鲁棒相位谱。",
  "2. Scaling Up AI-Generated Image Detection with Generator-Aware Prototypes：多生成器原型泛化。",
  "3. Zero-shot Detection of AI-Generated Image via RAW-RGB Alignment：物理成像链路零样本检测。",
  "4. Locate-Then-Examine：区域定位 + VLM 审查式推理。",
  "5. DGS-Net：CLIP 微调中的语义梯度手术。",
  "6. DNA：预训练模型内部伪迹神经元。",
  "7. MIRAGE：真实世界 benchmark 和 VLM 检测。",
  "8. Aligned Datasets Improve Detection of Latent Diffusion-Generated Images：数据对齐比复杂模型更关键。",
  "9. ForensicConcept：可迁移取证概念。",
  "10. OmniVL-Guard：多模态检测与定位的 balanced RL。",
  "11. FIND：极简快速 diffusion 检测 baseline。",
  "12. RealNet：real-only 无监督检测。",
  "",
  "## 7. 可发展研究问题",
  "",
  "1. **如何建立持续更新的数据闭环？** 自动收集 wild fake、新生成器样本和真实图，配合 replay buffer 做增量检测。",
  "2. **如何输出可信度而不是硬分类？** 对压缩、模糊、截图和低质量样本输出 detectability，支持拒判和专家路由。",
  "3. **如何把低层证据注入 MLLM？** 将相位、RAW-RGB、NPR、重建路径、concept codebook 转成可控 prompt 或 adapter。",
  "4. **如何审计 benchmark shortcut？** 系统检查 real/fake 的来源、分辨率、压缩、语义和平台链路是否对齐。",
  "5. **如何验证解释忠实性？** 要求解释绑定局部证据、mask、检索样本或概念原型，而不是只生成自然语言理由。",
  "",
].join("\n");

await fs.mkdir(path.dirname(OUT_PATH), { recursive: true });
await fs.writeFile(OUT_PATH, report, "utf8");
console.log(JSON.stringify({ out: OUT_PATH, papers: notes.length }, null, 2));
