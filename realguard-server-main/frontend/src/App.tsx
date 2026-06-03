import { FormEvent, Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  Counters,
  DeveloperApiKey,
  DeveloperTokenUsage,
  HistoryFilterKey,
  HistoryListResponse,
  HistoryRecord,
  ImageDetectionResult,
  RetrieveItem,
  User,
  VideoDetectionResult,
  createDeveloperApiKey,
  detectImage,
  detectVideo,
  downloadImageReport,
  downloadRetrieveReport,
  downloadVideoReport,
  getHistory,
  getDeveloperApiKeys,
  getDeveloperTokenUsage,
  getLibraries,
  getMe,
  getRetrievalHistory,
  loginByPassword,
  loginBySms,
  logout,
  registerUser,
  revokeDeveloperApiKey,
  retrieveSearch,
  getV2Health,
  runV2Detect,
  sendSmsCode
} from "./api";

type PageKey = "home" | "image" | "video" | "retrieve" | "history" | "developer";
type Status = { tone: "ok" | "error" | "info"; text: string } | null;
type AuthMode = "password" | "sms" | "register";
type HistoryTabKey = "image" | "video" | "imageRetrieve" | "videoRetrieve";
type HistorySummaryCard = { label: string; value: number | string; filterKey?: HistoryFilterKey };
type DeveloperSkillMode = "v2" | "v1";
type Lang = "zh" | "en";

const emptyCounters: Counters = {
  image_detect: 0,
  video_detect: 0,
  image_retrieve: 0,
  video_retrieve: 0
};
const HISTORY_PAGE_SIZE = 100;
const REALGUARD_PUBLIC_ORIGIN = typeof window === "undefined" ? "http://124.222.3.205" : window.location.origin;
const REALGUARD_API_BASE = `${REALGUARD_PUBLIC_ORIGIN}/v2-api`;
const REALGUARD_V1_API_BASE = `${REALGUARD_PUBLIC_ORIGIN}/api/developer/v1`;
const REALGUARD_SKILL_URL = `${REALGUARD_PUBLIC_ORIGIN}/skills/realguard-forensics/SKILL.md`;
const REALGUARD_API_DOC_URL = `${REALGUARD_PUBLIC_ORIGIN}/developer/API.md`;
const REALGUARD_SKILL_HANDOFF_V2 =
  `Use $realguard-forensics; read ${REALGUARD_SKILL_URL}; call POST ${REALGUARD_API_BASE}/detect with multipart field file and X-RealGuard-Key, or run python3 scripts/realguard_cli.py detect <file> --base-url ${REALGUARD_PUBLIC_ORIGIN} --api-prefix /v2-api --token <your-api-key> --pretty if the repo CLI is available; then return verdict, confidence, evidence, source, modelVersion, cacheVersion, tokenUsage, and reportId.`;
const REALGUARD_SKILL_HANDOFF_V1 =
  `Use $realguard-forensics in V1 mode; read ${REALGUARD_SKILL_URL}; call POST ${REALGUARD_V1_API_BASE}/detect with multipart field file and X-RealGuard-Key to use the RealGuard V1 image model; then return result.final_label, result.probability, result.confidence, result.visual_issues, result.itemid, and explain that V1 records call count but does not return tokenUsage.`;
const REALGUARD_SKILL_COMMAND_V2 =
  `python3 scripts/realguard_cli.py detect <file> --base-url ${REALGUARD_PUBLIC_ORIGIN} --api-prefix /v2-api --token <your-api-key> --pretty`;
const REALGUARD_SKILL_COMMAND_V1 =
  `curl -fsS -X POST ${REALGUARD_V1_API_BASE}/detect \\
  -H "X-RealGuard-Key: <your-api-key>" \\
  -F "file=@/path/to/image.png"`;
const UI_TEXT = {
  zh: {
    boot: "正在连接系统...",
    nav: {
      brand: "数字内容鉴伪平台",
      brandMobile: "鉴伪平台",
      home: "首页",
      functions: "功能",
      detection: "检测",
      retrieve: "检索",
      imageDetect: "图像鉴伪",
      videoDetect: "视频鉴伪",
      imageRetrieve: "图像侵权检索",
      videoRetrieve: "视频侵权检索",
      history: "历史记录",
      developer: "开发者平台",
      v2: "新版鉴伪",
      login: "登录",
      logout: "退出",
      logoutFull: "退出登录",
      loginRegister: "登录/注册",
      menu: "菜单",
      openMenu: "打开菜单",
      theme: "切换主题",
      language: "EN",
      languageAria: "切换到英文",
      mobileShort: {
        home: "首页",
        image: "图像",
        video: "视频",
        retrieve: "检索",
        history: "历史",
        developer: "开发",
      },
    },
    trial: {
      title: "访客体验",
      desc: "首次检测无需登录，第二次检测前请登录。",
      action: "登录/注册",
    },
    home: {
      eyebrow: "鉴伪情报平台",
      eyebrowNote: "面向智能体接入",
      titleLine1: "让生成内容鉴伪",
      titleLine2: "进入证据链",
      desc: "RealGuard 面向内容审核、版权检索和外部智能体，把图像和视频检测、取证证据、报告编号、密钥与用量统计组织成一条清晰流程。",
      primaryAction: "开始检测",
      secondaryAction: "接入开发者平台",
      trust1: "双链路鉴伪",
      trust2: "检测任务",
      trust3: "检索任务",
      briefingLabel: "实时证据简报",
      overall: "综合判断",
      risk: "生成风险 73.9%",
      support: "辅助证据",
      texture: "纹理与边缘异常",
      ela: "压缩误差图",
      elaSmall: "异常区域定位",
      noise: "噪声残差",
      noiseSmall: "生成纹理比对",
      usage: "调用与用量",
      usageSmall: "开发者成本审计",
      handoff: "智能体交接",
      workflowKicker: "从任务开始",
      workflowTitle: "四条路径，直接进入你要完成的工作。",
      workflowDesc: "审核人员从检测开始，版权团队从检索开始，外部智能体从技能开始，开发者从密钥和文档开始。",
      capabilitiesTitle: "核心能力",
      capabilitiesDesc: "检测、检索、报告与开发者接入保持独立，但在首页以同一条证据链呈现。",
      evidenceTitle: "结果不是一句话，而是一组可复核证据",
      evidenceDesc: "示例卡保留判断比例，但首页更强调报告、证据字段和后续追踪。",
    },
    workflow: [
      ["内容审核团队", "上传图像或视频，直接获得真伪结论、置信度、证据字段和报告编号。", "开始鉴伪"],
      ["版权检索场景", "对疑似侵权素材做相似内容检索，把重复传播和素材来源拉到同一视图。", "检索相似内容"],
      ["外部智能体", "复制公开技能，让 OpenClaw 或其他智能体使用 V2/V1 接口完成鉴伪。", "复制技能"],
      ["开发者接入", "生成个人密钥，追踪调用次数与用量成本，并在线调试返回结果。", "打开文档"],
    ],
    features: [
      ["图像鉴伪", "基于深度学习的图像真伪识别，支持多种场景的生成图像检测。"],
      ["视频鉴伪", "针对视频内容的生成检测与篡改识别，帧级分析定位可疑片段。"],
      ["图像侵权检索", "检索疑似侵权的图像，在图像数据库中快速定位可疑内容。"],
      ["视频侵权检索", "检索疑似侵权的视频，在数据库中快速定位相似可疑视频内容。"],
      ["新版鉴伪智能体", "独立新版系统，融合误差图、噪声残差等取证证据。"],
    ],
    examples: [
      ["案例一：泳池场景人物图像", "综合判断为生成图像（53.8%），点击查看检测结果。"],
      ["案例二：几何色块人像图像", "综合判断为生成图像（73.9%），点击查看检测结果。"],
    ],
    skillPanel: {
      badge1: "技能已接入",
      badge2: "外部智能体可调用",
      title: "把 RealGuard 变成外部智能体可直接调用的鉴伪工具",
      desc: "外部智能体不需要知道你的服务器目录，也不需要猜接口字段。复制下面的交接语后，它会先读取公网技能，再按 V2 多模态或 V1 图像模型输出可审计结论。",
      reasonTitle: "为什么必须公开技能",
      reason: "OpenClaw 等外部智能体访问不到本地仓库路径，也不知道报告字段、解释边界和鉴伪输出规范。公开技能后，任何智能体都能用同一份说明完成调用，并把调用次数与用量纳入审计。",
      protocol: [
        ["01 公开", "读取公网技能", "不依赖本地路径，外部智能体可访问"],
        ["02 选择", "选择模型链路", "默认 V2，V1 兼容旧图像模型"],
        ["03 审计", "返回证据与用量", "结论、报告、调用次数、用量统计"],
      ],
      terminalLabel: "推荐交接语",
      terminalStrong: "优先 V2，点击复制",
      copyV2Title: "复制 V2 技能调用",
      copyV2Desc: "优先推荐：多模态检测、报告、用量统计和模型版本更完整。",
      copyUrlTitle: "公开技能地址",
      copyUrlDesc: "给别的智能体的第一步：先读取公共说明。",
      copyV1Title: "复制 V1 技能调用",
      copyV1Desc: "兼容 RealGuard V1 图像模型，并统计调用次数。",
      openV2: "进入新版鉴伪",
      openDev: "打开开发者平台",
    },
    copy: {
      ready: "点击复制",
      copied: "已复制",
      copy: "复制",
      aria: "复制",
      prompt: "复制以下内容",
    },
    pages: {
      imageTitle: "图像鉴伪",
      imageDesc: "上传图片或选择示例图片，检测生成内容与可疑篡改痕迹。",
      videoTitle: "视频鉴伪",
      videoDesc: "上传本地视频或输入地址，检测视频真伪与可疑片段。",
      retrieveImageTitle: "图像侵权检索",
      retrieveVideoTitle: "视频侵权检索",
      retrieveDesc: "在数据库中检索视觉相似的可疑内容。",
      historyTitle: "历史记录",
      historyDesc: "查看检测与检索历史记录。",
    },
    developer: {
      docsBrandSmall: "开发者文档",
      badges: ["公开接口", "智能体技能", "双版本", "文件上传"],
      title: "RealGuard 开发者平台",
      desc: "面向 OpenClaw、外部智能体和业务系统的统一接入台。这里把密钥、公开技能、V1/V2 调用、用量统计和在线测试放在同一屏。",
      commands: ["签发密钥", "复制技能", "运行检测", "审计用量"],
      keyAction: "生成密钥",
      skillAction: "复制技能调用",
      skillsCopy: "技能复制",
      skillsCopyTitle: "点击即可复制给外部智能体",
      workflow: [
        ["生成密钥", "注册登录后生成个人密钥，绑定账号并可随时撤销。"],
        ["复制技能接入", "一键复制 V2 或 V1 交接语，让外部智能体直接使用。"],
        ["查看调用统计", "同时统计 V1/V2 调用次数、用量、缓存命中和端点分布。"],
        ["在线测试接口", "直接粘贴密钥、上传文件并查看返回结果。"],
      ],
      navGroups: ["开始使用", "接口参考", "开发工具", "资源"],
      navLinks: {
        overview: "总览",
        quickstart: "快速开始",
        skillCopy: "复制技能",
        auth: "认证",
        apiKeys: "密钥",
        tokenUsage: "调用统计",
        reference: "接口总览",
        detect: "检测",
        v1Detect: "V1 检测",
        forensics: "取证分析",
        provenance: "来源验证",
        reports: "报告",
        errors: "错误码",
        examples: "代码示例",
        console: "在线测试台",
        agentFields: "智能体字段",
        enterprise: "企业接入",
        resources: "公开资源",
      },
    },
    auth: {
      title: "账户登录",
      desc: "登录后 30 天内自动保持状态",
      password: "密码登录",
      sms: "验证码登录",
      register: "注册",
      phone: "手机号",
      username: "用户名",
      passwordLabel: "密码",
      smsCode: "短信验证码",
      phonePlaceholder: "请输入手机号",
      usernamePlaceholder: "请输入用户名",
      passwordPlaceholder: "请输入密码",
      smsPlaceholder: "请输入验证码",
      sendCode: "获取验证码",
      sending: "发送中",
      create: "创建账号",
      login: "登录",
    },
    footer: {
      brand: "数字内容鉴伪平台",
      copy: "© 2026 数字内容鉴伪平台",
    },
  },
  en: {
    boot: "Connecting to RealGuard...",
    nav: {
      brand: "Digital Content Forensics",
      brandMobile: "RealGuard",
      home: "Home",
      functions: "Tools",
      detection: "Detection",
      retrieve: "Retrieval",
      imageDetect: "Image Forensics",
      videoDetect: "Video Forensics",
      imageRetrieve: "Image Retrieval",
      videoRetrieve: "Video Retrieval",
      history: "History",
      developer: "Developer",
      v2: "V2 Agent",
      login: "Log in",
      logout: "Log out",
      logoutFull: "Log out",
      loginRegister: "Log in / Sign up",
      menu: "Menu",
      openMenu: "Open menu",
      theme: "Toggle theme",
      language: "中",
      languageAria: "Switch to Chinese",
      mobileShort: {
        home: "Home",
        image: "Image",
        video: "Video",
        retrieve: "Search",
        history: "History",
        developer: "Dev",
      },
    },
    trial: {
      title: "Guest access",
      desc: "Your first detection is free. Please log in before the second one.",
      action: "Log in / Sign up",
    },
    home: {
      eyebrow: "Forensic intelligence platform",
      eyebrowNote: "Agent-ready integration",
      titleLine1: "Bring AIGC forensics",
      titleLine2: "into the evidence chain",
      desc: "RealGuard organizes image and video detection, forensic evidence, report IDs, API keys, and usage metrics into a single workflow for reviewers, copyright teams, and external agents.",
      primaryAction: "Start detection",
      secondaryAction: "Open developer platform",
      trust1: "Dual V1 / V2 pipeline",
      trust2: "Detection tasks",
      trust3: "Retrieval tasks",
      briefingLabel: "Live evidence brief",
      overall: "Overall verdict",
      risk: "AI risk 73.9%",
      support: "Supporting evidence",
      texture: "Texture and edge anomalies",
      ela: "ELA map",
      elaSmall: "Suspicious region localization",
      noise: "Noise residual",
      noiseSmall: "Generated texture comparison",
      usage: "Calls and tokens",
      usageSmall: "Developer cost audit",
      handoff: "Agent handoff",
      workflowKicker: "Start from the task",
      workflowTitle: "Four paths into the work users actually need to finish.",
      workflowDesc: "Reviewers start with detection, copyright teams start with retrieval, agents start with skills, and developers start with keys and docs.",
      capabilitiesTitle: "Core capabilities",
      capabilitiesDesc: "Detection, retrieval, reports, and developer access stay separate but are presented as one evidence workflow.",
      evidenceTitle: "A result is not a sentence. It is a reviewable evidence set.",
      evidenceDesc: "Examples keep the probability view while emphasizing reports, evidence fields, and follow-up tracking.",
    },
    workflow: [
      ["Content review teams", "Upload images or videos and receive verdicts, confidence, evidence fields, and report IDs.", "Start forensics"],
      ["Copyright retrieval", "Search visually similar assets and inspect reposts, matches, and source clues in one view.", "Search similar content"],
      ["External agents", "Copy the public skill so OpenClaw or other agents can call V2/V1 APIs.", "Copy skill"],
      ["Developer integration", "Generate personal keys, track calls and usage cost, and debug JSON responses online.", "Open docs"],
    ],
    features: [
      ["Image Forensics", "Deep-learning image authenticity detection across generated-image scenarios."],
      ["Video Forensics", "AI-generation and tamper detection for videos with frame-level suspicious segment analysis."],
      ["Image Retrieval", "Find visually similar images and suspicious matches in image databases."],
      ["Video Retrieval", "Search suspicious videos and locate visually similar video content."],
      ["V2 Forensic Agent", "A newer standalone pipeline combining ELA, noise residuals, and other forensic evidence."],
    ],
    examples: [
      ["Case 1: Poolside person image", "Overall verdict: likely AI-generated (53.8%). Open the detection result."],
      ["Case 2: Geometric portrait image", "Overall verdict: likely AI-generated (73.9%). Open the detection result."],
    ],
    skillPanel: {
      badge1: "Skill integrated",
      badge2: "Callable by external agents",
      title: "Turn RealGuard into a forensics tool external agents can call directly",
      desc: "External agents do not need local server paths or guessed request fields. Copy the handoff text and the agent will read the public skill, then call either the V2 multimodal pipeline or V1 image model.",
      reasonTitle: "Why the skill must be public",
      reason: "External agents such as OpenClaw cannot access local repository paths and do not know report fields, output boundaries, or evidence requirements. A public skill gives every agent the same integration contract and audit expectations.",
      protocol: [
        ["01 Public", "Read public skill", "No local path dependency"],
        ["02 Choose", "Select model pipeline", "V2 by default, V1 for legacy image mode"],
        ["03 Audit", "Return evidence and usage", "Verdict, report, call count, usage metrics"],
      ],
      terminalLabel: "Recommended handoff",
      terminalStrong: "V2 first · click to copy",
      copyV2Title: "Copy V2 skill handoff",
      copyV2Desc: "Recommended: multimodal detection, reports, usage metrics, and model version.",
      copyUrlTitle: "Public skill URL",
      copyUrlDesc: "The first step for another agent: read the public instruction.",
      copyV1Title: "Copy V1 skill handoff",
      copyV1Desc: "Compatible with the RealGuard V1 image model and call-count tracking.",
      openV2: "Open V2 Agent",
      openDev: "Open developer platform",
    },
    copy: {
      ready: "Click to copy",
      copied: "Copied",
      copy: "Copy",
      aria: "Copy",
      prompt: "Copy this text",
    },
    pages: {
      imageTitle: "Image Forensics",
      imageDesc: "Upload an image or select a sample to detect generated content and suspicious tampering.",
      videoTitle: "Video Forensics",
      videoDesc: "Upload a local video or enter a URL to inspect authenticity and suspicious segments.",
      retrieveImageTitle: "Image Retrieval",
      retrieveVideoTitle: "Video Retrieval",
      retrieveDesc: "Search visually similar suspicious content in the database.",
      historyTitle: "History",
      historyDesc: "Review detection and retrieval records.",
    },
    developer: {
      docsBrandSmall: "Developer Docs",
      badges: ["Public API", "Agent Skill", "V1 / V2", "Multipart Upload"],
      title: "RealGuard Developer Platform",
      desc: "A unified integration desk for OpenClaw, external AI agents, and business systems, combining API keys, public skills, V1/V2 calls, usage metrics, and online testing.",
      commands: ["Issue key", "Copy skill", "Run detect", "Audit usage"],
      keyAction: "Generate API key",
      skillAction: "Copy skill handoff",
      skillsCopy: "Skills copy",
      skillsCopyTitle: "Click to copy for external agents",
      workflow: [
        ["Generate API key", "Create a personal key after registration, bind it to your account, and revoke it anytime."],
        ["Copy skill integration", "Copy a V2 or V1 handoff so external agents can use RealGuard directly."],
        ["Inspect usage", "Track V1/V2 calls, tokens, cache hits, and endpoint distribution."],
        ["Test API online", "Paste a key, upload a file, and inspect the returned JSON."],
      ],
      navGroups: ["Start", "API Reference", "Developer Tools", "Resources"],
      navLinks: {
        overview: "Overview",
        quickstart: "Quickstart",
        skillCopy: "Copy Skill",
        auth: "Authentication",
        apiKeys: "API Keys",
        tokenUsage: "Usage",
        reference: "Endpoint Index",
        detect: "Detect",
        v1Detect: "V1 Detect",
        forensics: "Forensics",
        provenance: "Provenance",
        reports: "Reports",
        errors: "Errors",
        examples: "Code Examples",
        console: "API Console",
        agentFields: "Agent Fields",
        enterprise: "Enterprise",
        resources: "Public Resources",
      },
    },
    auth: {
      title: "Account login",
      desc: "Stay signed in for 30 days",
      password: "Password",
      sms: "SMS code",
      register: "Sign up",
      phone: "Phone",
      username: "Username",
      passwordLabel: "Password",
      smsCode: "SMS code",
      phonePlaceholder: "Enter phone number",
      usernamePlaceholder: "Enter username",
      passwordPlaceholder: "Enter password",
      smsPlaceholder: "Enter code",
      sendCode: "Send code",
      sending: "Sending",
      create: "Create account",
      login: "Log in",
    },
    footer: {
      brand: "Digital Content Forensics",
      copy: "© 2026 Digital Content Forensics Platform",
    },
  },
} as const;
const IMAGE_MAX_BYTES = 25 * 1024 * 1024;
const VIDEO_MAX_BYTES = 512 * 1024 * 1024;
const V2_CONSOLE_MAX_BYTES = 25 * 1024 * 1024;

function formatUsageNumber(value: number | undefined | null, lang: Lang = "zh") {
  return Number(value || 0).toLocaleString(localeFor(lang));
}

function formatUsageDate(value: string | undefined | null, lang: Lang = "zh") {
  if (!value) return translate(lang, "暂无调用", "No calls yet");
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(localeFor(lang), { hour12: false });
}

function localeFor(lang: Lang) {
  return lang === "zh" ? "zh-CN" : "en-US";
}

function translate(lang: Lang, zh: string, en: string) {
  return lang === "zh" ? zh : en;
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [counters, setCounters] = useState<Counters>(emptyCounters);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<PageKey>(() => getInitialPage());
  const [authOpen, setAuthOpen] = useState(false);
  const [guestDetections, setGuestDetections] = useState(() => getGuestDetections());
  const [dark, setDark] = useState(() => getStorage()?.getItem("theme") === "dark");
  const [lang, setLang] = useState<Lang>(() => getInitialLang());
  const deviceType = useDeviceType();
  const text = UI_TEXT[lang];

  useEffect(() => {
    document.body.toggleAttribute("data-theme", dark);
    getStorage()?.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.title = lang === "zh" ? "数字内容鉴伪平台" : "Digital Content Forensics Platform";
    document.body.dataset.lang = lang;
    getStorage()?.setItem("realguard_lang", lang);
  }, [lang]);

  useEffect(() => {
    document.body.dataset.device = deviceType;
  }, [deviceType]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const elements = Array.from(document.querySelectorAll<HTMLElement>(".fade-up"));
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduceMotion || !("IntersectionObserver" in window)) {
      elements.forEach((element) => element.classList.add("visible"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("visible");
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.16, rootMargin: "0px 0px -8% 0px" }
    );

    elements.forEach((element, index) => {
      if (!element.style.getPropertyValue("--reveal-delay")) {
        element.style.setProperty("--reveal-delay", `${Math.min(index * 55, 360)}ms`);
      }
      observer.observe(element);
    });

    return () => observer.disconnect();
  }, [page]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (page === "home") params.delete("page");
    else params.set("page", page);
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}`);
  }, [page]);

  useEffect(() => {
    if (isDemoMode()) {
      setUser({ Userid: 1, username: "演示用户", phone: "13800000000", openid: "demo" });
      setCounters({ image_detect: 18, video_detect: 7, image_retrieve: 23, video_retrieve: 9 });
      setLoading(false);
      return;
    }
    refreshMe().finally(() => setLoading(false));
  }, []);

  async function refreshMe() {
    try {
      const data = await getMe();
      setUser(data.user);
      setCounters(data.counters || emptyCounters);
    } catch {
      setUser(null);
      setCounters(emptyCounters);
    }
  }

  async function handleLogout() {
    await logout().catch(() => undefined);
    setUser(null);
    setCounters(emptyCounters);
  }

  async function handleDetectionDone() {
    if (!user) {
      const next = guestDetections + 1;
      setGuestDetections(next);
      setGuestDetectionsStorage(next);
      return;
    }
    await refreshMe();
  }

  function requireAuth() {
    setAuthOpen(true);
  }

  if (loading) {
    return (
      <div className="boot-screen">
        <i className="fa fa-circle-o-notch fa-spin" />
        <span>{text.boot}</span>
      </div>
    );
  }

  return (
    <>
      <Nav
        page={page}
        setPage={setPage}
        user={user}
        dark={dark}
        setDark={setDark}
        lang={lang}
        setLang={setLang}
        onLogin={requireAuth}
        onLogout={handleLogout}
      />

      {!user && (
        <div className="trial-strip">
          <div className="container">
            <span className="trial-icon"><i className="fa fa-info-circle" /></span>
            <span className="trial-copy">
              <strong>{text.trial.title}</strong>
              <span>{text.trial.desc}</span>
            </span>
            <button onClick={requireAuth}>{text.trial.action}</button>
          </div>
        </div>
      )}

      {page === "home" && <HomePage counters={counters} setPage={setPage} lang={lang} />}
      {page === "image" && (
        <ImageDetectionPage
          lang={lang}
          isGuest={!user}
          guestDetections={guestDetections}
          onNeedAuth={requireAuth}
          onDone={handleDetectionDone}
        />
      )}
      {page === "video" && (
        <VideoDetectionPage
          lang={lang}
          isGuest={!user}
          guestDetections={guestDetections}
          onNeedAuth={requireAuth}
          onDone={handleDetectionDone}
        />
      )}
      {page === "retrieve" && <RetrievePage onDone={refreshMe} lang={lang} />}
      {page === "history" && <HistoryPage setPage={setPage} lang={lang} />}
      {page === "developer" && <DeveloperPlatformPage user={user} onNeedAuth={requireAuth} lang={lang} />}

      <Footer lang={lang} />

      {authOpen && (
        <AuthModal
          lang={lang}
          onClose={() => setAuthOpen(false)}
          onAuthed={async () => {
            await refreshMe();
            setAuthOpen(false);
          }}
        />
      )}
    </>
  );
}

function Nav({
  page,
  setPage,
  user,
  dark,
  setDark,
  lang,
  setLang,
  onLogin,
  onLogout
}: {
  page: PageKey;
  setPage: (page: PageKey) => void;
  user: User | null;
  dark: boolean;
  setDark: (value: boolean) => void;
  lang: Lang;
  setLang: (value: Lang) => void;
  onLogin: () => void;
  onLogout: () => void;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const text = UI_TEXT[lang].nav;
  const go = (next: PageKey) => {
    setPage(next);
    setMobileOpen(false);
  };
  const authAction = () => {
    setMobileOpen(false);
    if (user) onLogout();
    else onLogin();
  };

  return (
    <>
      <header className="nav">
        <div className="nav-inner">
          <button className="nav-logo" onClick={() => go("home")}>
            <i className="fa fa-eye" />
            <span className="logo-full">{text.brand}</span>
            <span className="logo-mobile">{text.brandMobile}</span>
          </button>
          <nav className="nav-links">
            <button className={page === "home" ? "active" : ""} onClick={() => go("home")}>
              {text.home}
            </button>
            <div className="dropdown">
              <button className={`dropdown-trigger ${["image", "video", "retrieve"].includes(page) ? "active" : ""}`}>
                <span>{text.functions}</span>
                <i className="fa fa-chevron-down" />
              </button>
              <div className="dropdown-menu">
                <div className="dropdown-label">{text.detection}</div>
                <button className={`dropdown-item ${page === "image" ? "active-item" : ""}`} onClick={() => go("image")}>
                  <i className="fa fa-image" /> {text.imageDetect}
                </button>
                <button className={`dropdown-item ${page === "video" ? "active-item" : ""}`} onClick={() => go("video")}>
                  <i className="fa fa-film" /> {text.videoDetect}
                </button>
                <div className="dropdown-divider" />
                <div className="dropdown-label">{text.retrieve}</div>
                <button className={`dropdown-item ${page === "retrieve" ? "active-item" : ""}`} onClick={() => go("retrieve")}>
                  <i className="fa fa-search" /> {text.imageRetrieve}
                </button>
                <button className="dropdown-item" onClick={() => go("retrieve")}>
                  <i className="fa fa-play-circle" /> {text.videoRetrieve}
                </button>
              </div>
            </div>
            <button className={page === "history" ? "active" : ""} onClick={() => go("history")}>
              {text.history}
            </button>
            <button className={page === "developer" ? "active" : ""} onClick={() => go("developer")}>
              {text.developer}
            </button>
            <button onClick={() => { window.location.href = "/v2/"; }}>{text.v2}</button>
            <button onClick={authAction}>{user ? text.logout : text.login}</button>
            <button
              className="language-toggle"
              title={text.languageAria}
              aria-label={text.languageAria}
              onClick={() => setLang(lang === "zh" ? "en" : "zh")}
            >
              {text.language}
            </button>
            <button className="theme-btn" title={text.theme} onClick={() => setDark(!dark)}>
              <i className={`fa ${dark ? "fa-sun-o" : "fa-moon-o"}`} />
            </button>
          </nav>
          <div className="mobile-nav-actions">
            <button
              className="language-toggle"
              title={text.languageAria}
              aria-label={text.languageAria}
              onClick={() => setLang(lang === "zh" ? "en" : "zh")}
            >
              {text.language}
            </button>
            <button className="theme-btn" title={text.theme} onClick={() => setDark(!dark)}>
              <i className={`fa ${dark ? "fa-sun-o" : "fa-moon-o"}`} />
            </button>
            <button className="mobile-menu-btn" aria-label={text.openMenu} onClick={() => setMobileOpen(!mobileOpen)}>
              <i className={`fa ${mobileOpen ? "fa-times" : "fa-bars"}`} />
              <span>{text.menu}</span>
            </button>
          </div>
        </div>
        <div className={`mobile-panel ${mobileOpen ? "open" : ""}`}>
          <button className={page === "home" ? "active" : ""} onClick={() => go("home")}><i className="fa fa-home" /> {text.home}</button>
          <button className={page === "image" ? "active" : ""} onClick={() => go("image")}><i className="fa fa-image" /> {text.imageDetect}</button>
          <button className={page === "video" ? "active" : ""} onClick={() => go("video")}><i className="fa fa-film" /> {text.videoDetect}</button>
          <button className={page === "retrieve" ? "active" : ""} onClick={() => go("retrieve")}><i className="fa fa-search" /> {text.imageRetrieve}</button>
          <button onClick={() => { window.location.href = "/v2/"; }}><i className="fa fa-bolt" /> {text.v2}</button>
          <button className={page === "history" ? "active" : ""} onClick={() => go("history")}><i className="fa fa-clock-o" /> {text.history}</button>
          <button className={page === "developer" ? "active" : ""} onClick={() => go("developer")}><i className="fa fa-code" /> {text.developer}</button>
          <button onClick={authAction}><i className={`fa ${user ? "fa-sign-out" : "fa-user"}`} /> {user ? text.logoutFull : text.loginRegister}</button>
        </div>
      </header>
      <nav className="mobile-bottom-nav">
        <button className={page === "home" ? "active" : ""} onClick={() => go("home")}><i className="fa fa-home" /><span>{text.mobileShort.home}</span></button>
        <button className={page === "image" ? "active" : ""} onClick={() => go("image")}><i className="fa fa-image" /><span>{text.mobileShort.image}</span></button>
        <button className={page === "video" ? "active" : ""} onClick={() => go("video")}><i className="fa fa-film" /><span>{text.mobileShort.video}</span></button>
        <button className={page === "retrieve" ? "active" : ""} onClick={() => go("retrieve")}><i className="fa fa-search" /><span>{text.mobileShort.retrieve}</span></button>
        <button className={page === "history" ? "active" : ""} onClick={() => go("history")}><i className="fa fa-clock-o" /><span>{text.mobileShort.history}</span></button>
        <button className={page === "developer" ? "active" : ""} onClick={() => go("developer")}><i className="fa fa-code" /><span>{text.mobileShort.developer}</span></button>
      </nav>
    </>
  );
}

function HomePage({ counters, setPage, lang }: { counters: Counters; setPage: (page: PageKey) => void; lang: Lang }) {
  const text = UI_TEXT[lang];
  const totalDetect = counters.image_detect + counters.video_detect || 10000;
  const totalRetrieve = counters.image_retrieve + counters.video_retrieve || 5000000;
  const workflowCards = [
    {
      step: "01",
      title: text.workflow[0][0],
      desc: text.workflow[0][1],
      action: text.workflow[0][2],
      icon: "fa-shield",
      onClick: () => setPage("image"),
    },
    {
      step: "02",
      title: text.workflow[1][0],
      desc: text.workflow[1][1],
      action: text.workflow[1][2],
      icon: "fa-crosshairs",
      onClick: () => setPage("retrieve"),
    },
    {
      step: "03",
      title: text.workflow[2][0],
      desc: text.workflow[2][1],
      action: text.workflow[2][2],
      icon: "fa-plug",
      onClick: () => setPage("developer"),
    },
    {
      step: "04",
      title: text.workflow[3][0],
      desc: text.workflow[3][1],
      action: text.workflow[3][2],
      icon: "fa-code",
      onClick: () => setPage("developer"),
    },
  ];

  return (
    <>
      <section className="home-hero-section">
        <div className="container home-hero-grid">
          <div className="home-hero-copy fade-up visible">
            <div className="home-eyebrow">
              <span>{text.home.eyebrow}</span>
              <i>{text.home.eyebrowNote}</i>
            </div>
            <h1>
              <span>{text.home.titleLine1}</span>
              <span>{text.home.titleLine2}</span>
            </h1>
            <p>{text.home.desc}</p>
            <div className="home-hero-actions">
              <button className="home-primary-action" onClick={() => setPage("image")}>
                {text.home.primaryAction} <i className="fa fa-arrow-right" />
              </button>
              <button className="home-secondary-action" onClick={() => setPage("developer")}>
                {text.home.secondaryAction} <i className="fa fa-code" />
              </button>
            </div>
            <div className="home-trust-row" aria-label={lang === "zh" ? "平台能力摘要" : "Platform capability summary"}>
              <div>
                <strong>V1 / V2</strong>
                <span>{text.home.trust1}</span>
              </div>
              <div>
                <strong>{totalDetect.toLocaleString(localeFor(lang))}+</strong>
                <span>{text.home.trust2}</span>
              </div>
              <div>
                <strong>{totalRetrieve.toLocaleString(localeFor(lang))}+</strong>
                <span>{text.home.trust3}</span>
              </div>
            </div>
          </div>

          <div className="home-briefing-board fade-up visible" aria-label={lang === "zh" ? "RealGuard 证据简报" : "RealGuard evidence brief"}>
            <div className="briefing-label">
              <span>{text.home.briefingLabel}</span>
              <b>RG-0427</b>
            </div>
            <div className="briefing-image-card primary">
              <img src="/system/case2.webp" alt={lang === "zh" ? "生成图像检测示例" : "Generated image detection sample"} />
              <div>
                <span>{text.home.overall}</span>
                <strong>{text.home.risk}</strong>
              </div>
            </div>
            <div className="briefing-image-card secondary">
              <img src="/system/case1.webp" alt={lang === "zh" ? "泳池场景检测示例" : "Poolside image detection sample"} />
              <div>
                <span>{text.home.support}</span>
                <strong>{text.home.texture}</strong>
              </div>
            </div>
            <div className="briefing-feed">
              <div><span>ELA</span><strong>{text.home.ela}</strong><small>{text.home.elaSmall}</small></div>
              <div><span>{lang === "zh" ? "噪声" : "Noise"}</span><strong>{text.home.noise}</strong><small>{text.home.noiseSmall}</small></div>
              <div><span>{lang === "zh" ? "用量" : "Usage"}</span><strong>{text.home.usage}</strong><small>{text.home.usageSmall}</small></div>
            </div>
            <div className="briefing-agent-card">
              <span>{text.home.handoff}</span>
              <code>Use $realguard-forensics · POST /v2-api/detect</code>
            </div>
          </div>
        </div>
      </section>

      <section className="section home-workflow-section">
        <div className="container">
          <div className="home-section-heading">
            <span>{text.home.workflowKicker}</span>
            <h2>{text.home.workflowTitle}</h2>
            <p>{text.home.workflowDesc}</p>
          </div>
          <div className="home-workflow-grid">
            {workflowCards.map((item) => (
              <button className="home-workflow-card" onClick={item.onClick} key={item.title}>
                <span>{item.step}</span>
                <i className={`fa ${item.icon}`} />
                <h3>{item.title}</h3>
                <p>{item.desc}</p>
                <strong>{item.action} <i className="fa fa-arrow-right" /></strong>
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="section skill-entry-section">
        <div className="container">
          <SkillEntryPanel lang={lang} />
        </div>
      </section>

      <section className="section section-alt home-capability-section">
        <div className="container">
          <SectionHeader title={text.home.capabilitiesTitle} desc={text.home.capabilitiesDesc} />
          <div className="features-grid">
            <FeatureCard accent="var(--primary)" icon="fa-image" title={text.features[0][0]} desc={text.features[0][1]} action={lang === "zh" ? "进入功能" : "Open tool"} onClick={() => setPage("image")} />
            <FeatureCard accent="var(--warning)" icon="fa-film" title={text.features[1][0]} desc={text.features[1][1]} action={lang === "zh" ? "进入功能" : "Open tool"} onClick={() => setPage("video")} />
            <FeatureCard accent="var(--accent)" icon="fa-search" title={text.features[2][0]} desc={text.features[2][1]} action={lang === "zh" ? "进入功能" : "Open tool"} onClick={() => setPage("retrieve")} />
            <FeatureCard accent="var(--primary-light)" icon="fa-play-circle" title={text.features[3][0]} desc={text.features[3][1]} action={lang === "zh" ? "进入功能" : "Open tool"} onClick={() => setPage("retrieve")} />
            <FeatureCard accent="var(--primary-dark)" icon="fa-bolt" title={text.features[4][0]} desc={text.features[4][1]} action={lang === "zh" ? "进入新版" : "Open V2"} onClick={() => { window.location.href = "/v2/"; }} />
          </div>
        </div>
      </section>

      <section className="section section-default">
        <div className="container">
          <SectionHeader title={text.home.evidenceTitle} desc={text.home.evidenceDesc} />
          <div className="examples-grid">
            <ExampleCard image="/system/case1.webp" title={text.examples[0][0]} desc={text.examples[0][1]} real={46.2} fake={53.8} lang={lang} />
            <ExampleCard image="/system/case2.webp" title={text.examples[1][0]} desc={text.examples[1][1]} real={26.1} fake={73.9} lang={lang} />
          </div>
        </div>
      </section>
    </>
  );
}

function SectionHeader({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="section-header fade-up visible">
      <h2 className="section-title">{title}</h2>
      <p className="section-desc">{desc}</p>
    </div>
  );
}

function FeatureCard({ accent, icon, title, desc, action, onClick }: { accent: string; icon: string; title: string; desc: string; action: string; onClick: () => void }) {
  return (
    <button className="feature-card fade-up visible" style={{ "--card-accent": accent } as React.CSSProperties} onClick={onClick}>
      <div className="feature-icon" style={{ background: colorBg(accent), color: accent }}>
        <i className={`fa ${icon}`} />
      </div>
      <h3>{title}</h3>
      <p>{desc}</p>
      <span className="feature-link">
        {action} <i className="fa fa-arrow-right" />
      </span>
    </button>
  );
}

function CopySnippetCard({
  id,
  title,
  desc,
  text,
  copiedId,
  onCopy,
  lang = "zh",
  variant = "default",
}: {
  id: string;
  title: string;
  desc: string;
  text: string;
  copiedId: string;
  onCopy: (id: string, text: string) => void;
  lang?: Lang;
  variant?: "default" | "primary" | "compact";
}) {
  const copied = copiedId === id;
  const copyText = UI_TEXT[lang].copy;
  const handleCopy = () => onCopy(id, text);
  return (
    <article
      className={`copy-snippet-card copy-snippet-card-${variant} ${copied ? "copied" : ""}`}
      role="button"
      tabIndex={0}
      onClick={handleCopy}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        handleCopy();
      }}
      aria-label={`${copyText.aria}: ${title}`}
    >
      <div className="copy-snippet-head">
        <span className="copy-snippet-status">{copied ? copyText.copied : copyText.ready}</span>
        <span className="copy-snippet-action" aria-hidden="true">
          {copied ? copyText.copied : copyText.copy}
        </span>
      </div>
      <strong>{title}</strong>
      <p>{desc}</p>
      <pre><code>{text}</code></pre>
    </article>
  );
}

async function copyTextToClipboard(text: string, promptLabel = "复制以下内容") {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the selection-based copy path for insecure contexts or denied clipboard permissions.
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (document.execCommand("copy")) {
      return true;
    }
  } catch {
    // Prompt is the final fallback when both browser copy paths fail.
  } finally {
    document.body.removeChild(textarea);
  }
  window.prompt(promptLabel, text);
  return false;
}

function SkillEntryPanel({ lang }: { lang: Lang }) {
  const [copiedId, setCopiedId] = useState("");
  const text = UI_TEXT[lang];

  async function copySkill(id: string, text: string) {
    try {
      await copyTextToClipboard(text, UI_TEXT[lang].copy.prompt);
    } finally {
      setCopiedId(id);
      window.setTimeout(() => setCopiedId(""), 2400);
    }
  }

  return (
    <div className="skill-entry-panel fade-up visible">
      <div className="skill-entry-main">
        <div className="skill-entry-badges">
          <span><i className="fa fa-plug" /> {text.skillPanel.badge1}</span>
          <span>{text.skillPanel.badge2}</span>
        </div>
        <h3>{text.skillPanel.title}</h3>
        <p>{text.skillPanel.desc}</p>
        <details className="skill-reason">
          <summary>{text.skillPanel.reasonTitle}</summary>
          <p>
            {text.skillPanel.reason} <code>{REALGUARD_SKILL_URL}</code>
          </p>
        </details>
        <div className="skill-protocol-rail" aria-label={lang === "zh" ? "技能接入流程" : "Skill integration flow"}>
          {text.skillPanel.protocol.map((item) => (
            <div key={item[0]}>
              <span>{item[0]}</span>
              <strong>{item[1]}</strong>
              <small>{item[2]}</small>
            </div>
          ))}
        </div>
      </div>
      <div className="skill-entry-code">
        <div className="skill-terminal-label">
          <span>{text.skillPanel.terminalLabel}</span>
          <strong>{text.skillPanel.terminalStrong}</strong>
        </div>
        <CopySnippetCard id="skill-v2" title={text.skillPanel.copyV2Title} desc={text.skillPanel.copyV2Desc} text={REALGUARD_SKILL_HANDOFF_V2} copiedId={copiedId} onCopy={copySkill} lang={lang} variant="primary" />
        <div className="skill-secondary-copy-grid">
          <CopySnippetCard id="skill-url" title={text.skillPanel.copyUrlTitle} desc={text.skillPanel.copyUrlDesc} text={REALGUARD_SKILL_URL} copiedId={copiedId} onCopy={copySkill} lang={lang} variant="compact" />
          <CopySnippetCard id="skill-v1" title={text.skillPanel.copyV1Title} desc={text.skillPanel.copyV1Desc} text={REALGUARD_SKILL_HANDOFF_V1} copiedId={copiedId} onCopy={copySkill} lang={lang} variant="compact" />
        </div>
        <div className="skill-cta-row">
          <button onClick={() => { window.location.href = "/v2/"; }}>
            {text.skillPanel.openV2} <i className="fa fa-arrow-right" />
          </button>
          <button onClick={() => { window.location.href = "/?page=developer"; }}>
            {text.skillPanel.openDev} <i className="fa fa-code" />
          </button>
        </div>
      </div>
    </div>
  );
}

function DeveloperPlatformPage({ user, onNeedAuth, lang }: { user: User | null; onNeedAuth: () => void; lang: Lang }) {
  const [apiKey, setApiKey] = useState("");
  const [keys, setKeys] = useState<DeveloperApiKey[]>([]);
  const [keyName, setKeyName] = useState(lang === "zh" ? "默认生产 Key" : "Default production key");
  const [generatedKey, setGeneratedKey] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);
  const [keyStatus, setKeyStatus] = useState<Status>(null);
  const [copiedDocId, setCopiedDocId] = useState("");
  const [skillMode, setSkillMode] = useState<DeveloperSkillMode>("v2");
  const [usageDays, setUsageDays] = useState(30);
  const [usage, setUsage] = useState<DeveloperTokenUsage | null>(null);
  const [usageBusy, setUsageBusy] = useState(false);
  const [usageStatus, setUsageStatus] = useState<Status>(null);
  const [fileType, setFileType] = useState("image");
  const [testFile, setTestFile] = useState<File | null>(null);
  const [consoleBusy, setConsoleBusy] = useState(false);
  const [consoleStatus, setConsoleStatus] = useState<Status>(null);
  const [consoleResult, setConsoleResult] = useState<Record<string, unknown> | null>(null);
  const [consoleMeta, setConsoleMeta] = useState<{ endpoint: string; elapsedMs: number; at: string } | null>(null);
  const text = UI_TEXT[lang].developer;
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  useEffect(() => {
    if (!user) {
      setKeys([]);
      setGeneratedKey("");
      setKeyStatus(null);
      setUsage(null);
      setUsageStatus(null);
      return;
    }
    void loadDeveloperKeys();
    void loadDeveloperUsage(usageDays);
  }, [user?.Userid]);

  useEffect(() => {
    if (!user) return;
    void loadDeveloperUsage(usageDays);
  }, [usageDays]);

  async function loadDeveloperKeys() {
    try {
      const data = await getDeveloperApiKeys();
      setKeys(data.keys || []);
    } catch (error) {
      setKeyStatus({ tone: "error", text: errorMessage(error) });
    }
  }

  async function loadDeveloperUsage(days = usageDays) {
    if (!user) return;
    setUsageBusy(true);
    setUsageStatus(null);
    try {
      const data = await getDeveloperTokenUsage(days);
      setUsage(data.usage);
    } catch (error) {
      setUsageStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setUsageBusy(false);
    }
  }

  async function handleCreateKey() {
    if (!user) {
      onNeedAuth();
      return;
    }
    setKeyBusy(true);
    setGeneratedKey("");
    setKeyStatus({ tone: "info", text: lang === "zh" ? "正在生成密钥..." : "Generating API key..." });
    try {
      const data = await createDeveloperApiKey(keyName);
      setGeneratedKey(data.apiKey);
      setApiKey(data.apiKey);
      setKeys((current) => [data.key, ...current.filter((item) => item.id !== data.key.id)]);
      setKeyStatus({ tone: "ok", text: lang === "zh" ? "密钥已生成。完整密钥只显示一次，请立即复制保存。" : "API key generated. The full key is shown only once. Copy it now." });
    } catch (error) {
      setKeyStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setKeyBusy(false);
    }
  }

  async function handleRevokeKey(keyId: number) {
    setKeyBusy(true);
    setKeyStatus({ tone: "info", text: lang === "zh" ? "正在撤销密钥..." : "Revoking API key..." });
    try {
      await revokeDeveloperApiKey(keyId);
      await loadDeveloperKeys();
      setKeyStatus({ tone: "ok", text: lang === "zh" ? "密钥已撤销，后续请求会被拒绝。" : "API key revoked. Future requests will be rejected." });
    } catch (error) {
      setKeyStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setKeyBusy(false);
    }
  }

  async function copyDeveloperText(id: string, text: string) {
    try {
      await copyTextToClipboard(text, UI_TEXT[lang].copy.prompt);
    } finally {
      setCopiedDocId(id);
      window.setTimeout(() => setCopiedDocId(""), 2400);
    }
  }
  const docsNavGroups = [
    {
      title: text.navGroups[0],
      links: [
        ["#overview", text.navLinks.overview],
        ["#quickstart", text.navLinks.quickstart],
        ["#skill-copy", text.navLinks.skillCopy],
        ["#auth", text.navLinks.auth],
        ["#api-keys", text.navLinks.apiKeys],
        ["#token-usage", text.navLinks.tokenUsage],
      ],
    },
    {
      title: text.navGroups[1],
      links: [
        ["#reference", text.navLinks.reference],
        ["#detect", text.navLinks.detect],
        ["#v1-detect", text.navLinks.v1Detect],
        ["#forensics", text.navLinks.forensics],
        ["#provenance", text.navLinks.provenance],
        ["#reports", text.navLinks.reports],
        ["#errors", text.navLinks.errors],
      ],
    },
    {
      title: text.navGroups[2],
      links: [
        ["#examples", text.navLinks.examples],
        ["#console", text.navLinks.console],
        ["#agent-fields", text.navLinks.agentFields],
      ],
    },
    {
      title: text.navGroups[3],
      links: [
        ["#enterprise", text.navLinks.enterprise],
        ["#resources", text.navLinks.resources],
      ],
    },
  ];
  const endpoints = [
    { method: "GET", path: "/health", title: "Health", desc: tr("公开服务状态、能力摘要、上传限制和访问保护状态。", "Public service health, capability summary, upload limits, and access protection status."), anchor: "#health" },
    { method: "GET", path: "/admin/health", title: "Admin Health", desc: tr("受保护的详细诊断接口，返回模型、校准、存储等内部状态。", "Protected diagnostics for model, calibration, storage, and internal service status."), anchor: "#admin-health" },
    { method: "POST", path: "/detect", title: "V2 Detect", desc: tr("V2 多模态鉴伪接口。multipart 上传 file，可选 fileType。", "V2 multimodal forensics endpoint. Upload file with multipart/form-data and optional fileType."), anchor: "#detect" },
    { method: "POST", path: "/api/developer/v1/detect", title: "V1 Detect", desc: tr("V1 图像模型接口。multipart 上传 file，记录调用次数。", "V1 image model endpoint. Upload file with multipart/form-data; calls are counted."), anchor: "#v1-detect" },
    { method: "POST", path: "/forensics", title: "Forensics", desc: tr("图像可解释性取证分析，返回 ELA、噪声、频域等证据。", "Image explainability forensics returning ELA, noise, frequency-domain, and related evidence."), anchor: "#forensics" },
    { method: "POST", path: "/provenance", title: "Provenance", desc: tr("图像 C2PA / SynthID / 内容凭证验证。", "C2PA, SynthID, visible watermark, and content-credential verification."), anchor: "#provenance" },
    { method: "GET", path: "/report/{reportId}/download", title: "Report Download", desc: tr("下载 HTML 或结构化报告，用于审计归档。", "Download HTML or structured reports for audit archives."), anchor: "#reports" },
  ];
  const requestParams = [
    ["file", "File", tr("必填", "required"), tr("待检测文件。支持图片、视频、音频、文档；用 multipart/form-data 上传。", "File to analyze. Supports images, videos, audio, and documents via multipart/form-data.")],
    ["fileType", "string", tr("选填", "optional"), tr("文件类型提示：image、video、audio、document。未传时服务会尝试自动推断。", "File type hint: image, video, audio, or document. The service infers it when omitted.")],
  ];
  const reportPathParams = [
    ["reportId", "string", tr("必填", "required"), tr("检测结果返回的报告编号，例如 RJ-RPT-20260602-0001。", "Report ID returned by detection, for example RJ-RPT-20260602-0001.")],
  ];
  const fields = [
    ["agentSummary", tr("给智能体优先使用的结构化摘要。", "Structured summary intended for agent output.")],
    ["verdict", tr("鉴伪结论，例如 real / suspected / likely_ai_generated / unknown。", "Forensic verdict, for example real, suspected, likely_ai_generated, or unknown.")],
    ["confidence", tr("0-1 置信度，展示时可换算百分比。", "Confidence from 0 to 1; convert to a percentage for display.")],
    ["modelVersion", tr("模型或规则链路版本。", "Model or rule-chain version.")],
    ["cacheVersion", tr("分析缓存版本，用于判断结果是否来自同一分析逻辑。", "Analysis cache version, useful for comparing whether results used the same logic.")],
    ["tokenUsage", tr("本次模型调用的 prompt / completion / total token；缓存命中时为 0。", "Prompt, completion, and total tokens for this model call; zero on cache hits.")],
    ["source", tr("vlm / mock / heuristic 等，决定结果可信度说明。", "Source such as vlm, mock, or heuristic, which determines how limitations should be explained.")],
    ["reportId", tr("可用于下载和归档报告的编号。", "ID used to download and archive the report.")],
    ["synthid / visibleWatermark", tr("水印、SynthID、可见水印等附加证据。", "Additional evidence such as watermark, SynthID, or visible watermark signals.")],
  ];
  const v1Fields = [
    ["result.final_label", tr("V1 图像模型输出的最终标签，例如 AI生成图像 / 真实图像。", "Final V1 image-model label, for example AI-generated image or real image.")],
    ["result.probability", tr("V1 置信概率，用于排序和阈值判断。", "V1 confidence probability for ranking and threshold decisions.")],
    ["result.confidence", tr("V1 置信等级。", "V1 confidence level.")],
    ["result.visual_issues", tr("图像可疑区域、视觉问题或辅助证据。", "Suspicious regions, visual issues, or supporting evidence.")],
    ["result.itemid", tr("V1 站内报告和历史记录使用的编号。", "V1 item ID used for site reports and history records.")],
  ];
  const errorRows = [
    ["400", "Bad Request", tr("缺少 file、fileType 不合法或 multipart 格式错误。", "Missing file, invalid fileType, or malformed multipart body.")],
    ["401", "Unauthorized", tr("需要 API Key 的接口未传 Key、Key 无效或已撤销。", "The endpoint requires an API key, or the key is invalid or revoked.")],
    ["403", "Forbidden", tr("API Key 无权访问该报告或资源。", "The API key is not allowed to access this report or resource.")],
    ["413", "Payload Too Large", tr("文件超过服务允许大小，需要压缩或走异步/分片流程。", "The file exceeds the service limit. Compress it or use an async/chunked flow.")],
    ["422", "Unprocessable Entity", tr("文件格式无法识别或不支持当前检测链路。", "The file format cannot be recognized or is unsupported by the selected pipeline.")],
    ["500", "Internal Server Error", tr("服务端分析失败；记录 taskId 并重试或转人工处理。", "Server-side analysis failed. Record the taskId, then retry or escalate to manual review.")],
  ];
  const jsExample = `const form = new FormData();
form.append("file", fileInput.files[0]);
form.append("fileType", "image");

const res = await fetch("${REALGUARD_API_BASE}/detect", {
  method: "POST",
  headers: { "X-RealGuard-Key": apiKey },
  body: form
});
const data = await res.json();
console.log(data.agentSummary || data);`;
  const curlDetectExample = `curl -fsS -X POST ${REALGUARD_API_BASE}/detect \\
  -H "X-RealGuard-Key: <your-api-key>" \\
  -F "file=@/path/to/file.png" \\
  -F "fileType=image"`;
  const curlV1DetectExample = REALGUARD_SKILL_COMMAND_V1;
  const curlHealthExample = `curl -fsS ${REALGUARD_API_BASE}/health`;
  const curlForensicsExample = `curl -fsS -X POST ${REALGUARD_API_BASE}/forensics \\
  -H "X-RealGuard-Key: <your-api-key>" \\
  -F "file=@/path/to/image.png"`;
  const curlProvenanceExample = `curl -fsS -X POST ${REALGUARD_API_BASE}/provenance \\
  -H "X-RealGuard-Key: <your-api-key>" \\
  -F "file=@/path/to/image.png"`;
  const curlReportExample = `curl -fsS ${REALGUARD_API_BASE}/report/<reportId>/download \\
  -H "X-RealGuard-Key: <your-api-key>" \\
  -o realguard-report.html`;
  const pythonExample = `import requests

url = "${REALGUARD_API_BASE}/detect"
headers = {"X-RealGuard-Key": api_key}
with open("/path/to/file.png", "rb") as f:
    r = requests.post(url, headers=headers, files={"file": f}, data={"fileType": "image"})
r.raise_for_status()
print(r.json().get("agentSummary") or r.json())`;
  const cliExample = REALGUARD_SKILL_COMMAND_V2;

  const runHealthCheck = async () => {
    const started = performance.now();
    setConsoleBusy(true);
    setConsoleStatus({ tone: "info", text: tr("正在检查 V2 API 状态...", "Checking V2 API status...") });
    try {
      const result = await getV2Health(apiKey);
      setConsoleResult(result as Record<string, unknown>);
      setConsoleMeta({ endpoint: "GET /health", elapsedMs: Math.round(performance.now() - started), at: new Date().toLocaleString(localeFor(lang), { hour12: false }) });
      setConsoleStatus({ tone: "ok", text: tr("健康检查成功。", "Health check succeeded.") });
    } catch (error) {
      setConsoleStatus({ tone: "error", text: error instanceof Error ? error.message : tr("健康检查失败", "Health check failed") });
    } finally {
      setConsoleBusy(false);
    }
  };

  const runDetectTest = async () => {
    if (!testFile) {
      setConsoleStatus({ tone: "error", text: tr("请先选择要测试的文件。", "Select a file to test first.") });
      return;
    }
    const message = validateFile(testFile, { kind: tr("测试文件", "test file"), maxBytes: V2_CONSOLE_MAX_BYTES, lang });
    if (message) {
      setConsoleStatus({ tone: "error", text: message });
      return;
    }
    const started = performance.now();
    setConsoleBusy(true);
    setConsoleStatus({ tone: "info", text: tr("正在上传文件并调用鉴伪 API...", "Uploading file and calling the forensics API...") });
    try {
      const result = await runV2Detect({ file: testFile, fileType, token: apiKey });
      setConsoleResult(result as Record<string, unknown>);
      setConsoleMeta({ endpoint: "POST /detect", elapsedMs: Math.round(performance.now() - started), at: new Date().toLocaleString(localeFor(lang), { hour12: false }) });
      setConsoleStatus({ tone: "ok", text: tr(`检测完成：${result.verdict || "已返回结果"}`, `Detection complete: ${result.verdict || "result returned"}`) });
      void loadDeveloperUsage(usageDays);
    } catch (error) {
      setConsoleStatus({ tone: "error", text: error instanceof Error ? error.message : tr("检测失败", "Detection failed") });
    } finally {
      setConsoleBusy(false);
    }
  };

  const renderedResult = consoleResult ? JSON.stringify(consoleResult, null, 2) : "";
  const usageSummary = usage?.summary;
  const recentUsageDays = (usage?.byDay || []).slice(-7);
  const totalCalls = Number(usageSummary?.totalCalls ?? usageSummary?.totalRequests ?? 0);
  const v1Calls = Number(usageSummary?.v1Calls ?? 0);
  const v2Calls = Number(usageSummary?.v2Calls ?? Math.max(0, totalCalls - v1Calls));
  const maxDayCalls = Math.max(1, ...recentUsageDays.map((item) => Number(item.requests || 0)));
  const endpointUsage = usage?.byEndpoint || [];
  const pipelineUsage = usage?.byPipeline?.length
    ? usage.byPipeline
    : [
        { pipeline: "v1", requests: v1Calls, promptTokens: 0, completionTokens: 0, totalTokens: 0 },
        { pipeline: "v2", requests: v2Calls, promptTokens: 0, completionTokens: 0, totalTokens: Number(usageSummary?.totalTokens || 0) },
      ];
  const skillOptions = [
    {
      id: "v2" as DeveloperSkillMode,
      title: lang === "zh" ? "V2 多模态技能调用" : "V2 multimodal skill handoff",
      desc: lang === "zh" ? "推荐默认使用：返回摘要、报告编号、模型版本和用量统计。" : "Recommended by default: returns summary, report ID, model version, and usage metrics.",
      endpoint: "POST /v2-api/detect",
      text: REALGUARD_SKILL_HANDOFF_V2,
    },
    {
      id: "v1" as DeveloperSkillMode,
      title: lang === "zh" ? "V1 图像模型技能调用" : "V1 image-model skill handoff",
      desc: lang === "zh" ? "用于兼容旧图像鉴伪链路，统计调用次数，响应使用 result.* 字段。" : "For the legacy image forensics pipeline with call-count tracking and result.* response fields.",
      endpoint: "POST /api/developer/v1/detect",
      text: REALGUARD_SKILL_HANDOFF_V1,
    },
  ];
  const activeSkill = skillOptions.find((item) => item.id === skillMode) || skillOptions[0];
  const developerActionCards = [
    { href: "#api-keys", icon: "fa-key", title: text.workflow[0][0], desc: text.workflow[0][1] },
    { href: "#skill-copy", icon: "fa-copy", title: text.workflow[1][0], desc: text.workflow[1][1] },
    { href: "#token-usage", icon: "fa-line-chart", title: text.workflow[2][0], desc: text.workflow[2][1] },
    { href: "#console", icon: "fa-terminal", title: text.workflow[3][0], desc: text.workflow[3][1] },
  ];

  return (
    <main className="main developer-docs-page">
      <div className="container developer-platform docs-platform">
        <div className="docs-shell">
          <aside className="docs-sidebar" aria-label={tr("开发者文档目录", "Developer documentation navigation")}>
            <a className="docs-brand" href="#overview">
              <span><i className="fa fa-shield" /></span>
              <div>
                <strong>RealGuard API</strong>
                <small>{text.docsBrandSmall}</small>
              </div>
            </a>
            {docsNavGroups.map((group) => (
              <div className="docs-sidebar-group" key={group.title}>
                <div className="docs-sidebar-title">{group.title}</div>
                {group.links.map(([href, label]) => (
                  <a key={href} href={href}>{label}</a>
                ))}
              </div>
            ))}
            <div className="docs-sidebar-card">
              <span>V2 Base URL</span>
              <code>{REALGUARD_API_BASE}</code>
              <span>V1 Base URL</span>
              <code>{REALGUARD_V1_API_BASE}</code>
            </div>
          </aside>

          <article className="docs-main">
            <section id="overview" className="docs-section docs-hero-section developer-workbench">
              <div className="docs-hero-copy developer-workbench-copy">
                <div className="developer-badges">
                  {text.badges.map((badge) => <span key={badge}>{badge}</span>)}
                </div>
                <h1>{text.title}</h1>
                <p>{text.desc}</p>
                <div className="developer-command-strip" aria-label={tr("开发者接入流程", "Developer integration flow")}>
                  {text.commands.map((item, index) => (
                    <span key={item}><b>{String(index + 1).padStart(2, "0")}</b> {item}</span>
                  ))}
                </div>
                <div className="docs-hero-actions">
                  <a href="#api-keys">{text.keyAction}</a>
                  <a href="#skill-copy" className="secondary">{text.skillAction}</a>
                </div>
              </div>
              <div id="skill-copy" className="developer-skill-console">
                <div className="developer-console-ruler" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                </div>
                <div className="developer-skill-console-head">
                  <div>
                    <span>{text.skillsCopy}</span>
                    <strong>{text.skillsCopyTitle}</strong>
                  </div>
                  <code>{activeSkill.endpoint}</code>
                </div>
                <div className="skill-mode-toggle" role="tablist" aria-label={tr("选择技能调用模式", "Select skill call mode")}>
                  {skillOptions.map((item) => (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={skillMode === item.id}
                      className={skillMode === item.id ? "active" : ""}
                      onClick={() => setSkillMode(item.id)}
                      key={item.id}
                    >
                      {item.id.toUpperCase()}
                    </button>
                  ))}
                </div>
                <CopySnippetCard
                  id={`developer-skill-${activeSkill.id}`}
                  title={activeSkill.title}
                  desc={activeSkill.desc}
                  text={activeSkill.text}
                  copiedId={copiedDocId}
                  onCopy={copyDeveloperText}
                  lang={lang}
                  variant="primary"
                />
              </div>
            </section>

            <nav className="developer-workflow-strip" aria-label={tr("开发者接入工作流", "Developer integration workflow")}>
              {developerActionCards.map((item) => (
                <a href={item.href} key={item.href}>
                  <i className={`fa ${item.icon}`} />
                  <strong>{item.title}</strong>
                  <small>{item.desc}</small>
                </a>
              ))}
            </nav>

            <section id="quickstart" className="docs-section">
              <div className="docs-section-kicker">{tr("开始使用", "Getting Started")}</div>
              <h2>{tr("快速开始", "Quickstart")}</h2>
              <p className="docs-lead">
                {tr(
                  "第一次接入只需要三步：读取公开技能、按场景选择 V2 多模态或 V1 图像模型、按关键字段输出可审计结论。",
                  "First integration takes three steps: read the public skill, choose V2 multimodal or V1 image model for the scenario, then output an auditable result from key fields."
                )}
                {" "}
                {tr("V2 读取", "For V2 read")} <code>agentSummary</code> / <code>tokenUsage</code>
                {tr("，V1 读取", "; for V1 read")} <code>result.final_label</code> / <code>result.itemid</code>{tr("。", ".")}
              </p>
              <div className="docs-callout docs-callout-strong">
                <h3>{tr("为什么必须公开技能", "Why the skill must be public")}</h3>
                <p>
                  {tr(
                    "别的智能体访问不到你的本地路径，也无法猜测接口字段、报告下载地址和解释边界。公开技能后，OpenClaw 只要读取公网地址，就能稳定调用接口，并知道哪些字段必须带入最终鉴伪结论。",
                    "Other agents cannot access your local paths or infer request fields, report download URLs, and explanation boundaries. With a public skill, OpenClaw can read the public URL, call the API reliably, and include the required fields in the final forensics result."
                  )}
                </p>
                <div className="docs-copy-grid">
                  <CopySnippetCard id="docs-skill-url" title={UI_TEXT[lang].skillPanel.copyUrlTitle} desc={UI_TEXT[lang].skillPanel.copyUrlDesc} text={REALGUARD_SKILL_URL} copiedId={copiedDocId} onCopy={copyDeveloperText} lang={lang} variant="compact" />
                  <CopySnippetCard id="docs-skill-v2" title={UI_TEXT[lang].skillPanel.copyV2Title} desc={UI_TEXT[lang].skillPanel.copyV2Desc} text={REALGUARD_SKILL_HANDOFF_V2} copiedId={copiedDocId} onCopy={copyDeveloperText} lang={lang} variant="primary" />
                  <CopySnippetCard id="docs-skill-v1" title={UI_TEXT[lang].skillPanel.copyV1Title} desc={UI_TEXT[lang].skillPanel.copyV1Desc} text={REALGUARD_SKILL_HANDOFF_V1} copiedId={copiedDocId} onCopy={copyDeveloperText} lang={lang} variant="compact" />
                </div>
              </div>
              <div className="docs-code-block">
                <div className="docs-code-title"><span className="method method-post">POST</span><span>{tr("首次调用 /detect", "First /detect call")}</span></div>
                <pre>{curlDetectExample}</pre>
              </div>
            </section>

            <section id="auth" className="docs-section">
              <div className="docs-section-kicker">Authentication</div>
              <h2>{tr("认证", "Authentication")}</h2>
              <p className="docs-lead">
                {tr("调用", "Use a personal API key generated in the developer platform when calling")} <code>/detect</code>{tr("、", ", ")}<code>/forensics</code>{tr("、", ", ")}<code>/provenance</code>
                {tr(" 和报告下载接口时，请使用开发者平台生成的个人 API Key。每个 Key 绑定到登录用户，可撤销、可审计。", " and report-download endpoints. Each key is bound to the signed-in user and can be revoked and audited.")}
              </p>
              <div className="docs-code-block compact">
                <pre>{`X-RealGuard-Key: rg_sk_xxx
Authorization: Bearer rg_sk_xxx`}</pre>
              </div>
              <div className="docs-callout">
                <strong>{tr("安全建议", "Security guidance")}</strong>
                <p>{tr("API Key 不要写进前端源码或公开仓库。自动化智能体应使用独立 Key，并记录调用人、时间、文件摘要和报告 ID。", "Do not put API keys in frontend source code or public repositories. Automated agents should use dedicated keys and log caller, time, file digest, and report ID.")}</p>
              </div>
              <div className="docs-callout">
                <strong>{tr("运维 Token", "Operations token")}</strong>
                <p>
                  <code>X-Jianzhen-Token</code>
                  {tr(" 仅用于 ", " is only for ")}<code>/admin/health</code>{tr("、", ", ")}<code>/history</code>{tr("、", ", ")}<code>/metrics</code>
                  {tr(" 等管理接口，不应发给普通开发者或外部智能体。", " and other administrative endpoints. Do not share it with normal developers or external agents.")}
                </p>
              </div>
            </section>

            <section id="api-keys" className="docs-section auth-manager-section">
              <div className="docs-section-kicker">API Key Management</div>
              <h2>{tr("我的 API Key", "My API Keys")}</h2>
              <p className="docs-lead">
                {tr("注册并登录开发者平台后，可以生成自己的", "After registration and login, generate your own")} <code>rg_sk_</code> Key{tr("。", ". ")}
                {tr("完整 Key 只在创建时显示一次；列表中只保留预览、状态和最后使用时间。", "The full key is shown only once at creation; the list keeps only preview, status, and last-used time.")}
              </p>
              {!user ? (
                <div className="docs-callout docs-callout-strong">
                  <h3>{tr("需要先注册/登录", "Sign up or log in first")}</h3>
                  <p>{tr("API Key 需要绑定到真实账号，用于调用审计、撤销和报告权限控制。", "API keys must be bound to a real account for call audit, revocation, and report access control.")}</p>
                  <button className="docs-inline-button" onClick={onNeedAuth}>{tr("注册/登录开发者平台", "Sign up / log in to developer platform")}</button>
                </div>
              ) : (
                <>
                  <div className="api-key-manager">
                    <div className="api-key-create">
                      <div>
                        <span>{tr("当前账号", "Current account")}</span>
                        <strong>{user.username || user.phone}</strong>
                        <small>{user.phone}</small>
                      </div>
                      <label>
                        {tr("Key 名称", "Key name")}
                        <input value={keyName} maxLength={120} onChange={(event) => setKeyName(event.target.value)} />
                      </label>
                      <button disabled={keyBusy} onClick={handleCreateKey}>
                        <i className={`fa ${keyBusy ? "fa-spinner detect-spin" : "fa-key"}`} /> {tr("生成 API Key", "Generate API key")}
                      </button>
                      {keyStatus && <StatusPill status={keyStatus} />}
                      {generatedKey && (
                        <div className="generated-key-box">
                          <span>{tr("完整 Key 只显示一次", "The full key is shown only once")}</span>
                          <code>{generatedKey}</code>
                          <div>
                            <button onClick={() => copyDeveloperText("generated-key", generatedKey)}>
                              {copiedDocId === "generated-key" ? UI_TEXT[lang].copy.copied : tr("复制 Key", "Copy key")}
                            </button>
                            <button onClick={() => setApiKey(generatedKey)}>{tr("填入测试台", "Fill into console")}</button>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="api-key-list">
                      <div className="api-key-list-head">
                        <strong>{tr("已创建 Key", "Created keys")}</strong>
                        <button disabled={keyBusy} onClick={loadDeveloperKeys}>{tr("刷新", "Refresh")}</button>
                      </div>
                      {keys.length === 0 ? (
                        <p className="empty-key-state">{tr("暂无 API Key。生成后即可在外部智能体或业务系统中调用接口。", "No API keys yet. Generate one to call the API from external agents or business systems.")}</p>
                      ) : (
                        keys.map((item) => (
                          <div className={`api-key-row ${item.status === "active" ? "active" : "revoked"}`} key={item.id}>
                            <div>
                              <strong>{item.name}</strong>
                              <code>{item.preview}</code>
                              <span>
                                {tr("创建：", "Created: ")}{item.createdAt || "-"} · {tr("最后使用：", "Last used: ")}{item.lastUsedAt || tr("未使用", "Never")}
                              </span>
                            </div>
                            <div>
                              <small>{item.status}</small>
                              {item.status === "active" && (
                                <button disabled={keyBusy} onClick={() => handleRevokeKey(item.id)}>{tr("撤销", "Revoke")}</button>
                              )}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  <div id="token-usage" className="token-usage-panel">
                    <div className="token-usage-header">
                      <div>
                        <div className="docs-section-kicker">Usage Analytics</div>
                        <h3>{tr("调用次数与用量统计", "Calls and usage analytics")}</h3>
                        <p>
                          {tr(
                            "统计不只看 Token，也要看调用次数。V1 图像模型和 V2 多模态接口会合并展示，便于发现外部智能体重试、Key 滥用、缓存命中和真实模型成本变化。",
                            "Usage analytics track both tokens and call counts. V1 image-model calls and V2 multimodal calls are shown together so retries, key misuse, cache hits, and real model cost changes are visible."
                          )}
                        </p>
                      </div>
                      <div className="token-usage-actions">
                        <select value={usageDays} onChange={(event) => setUsageDays(Number(event.target.value))}>
                          <option value={7}>{tr("近 7 天", "Last 7 days")}</option>
                          <option value={14}>{tr("近 14 天", "Last 14 days")}</option>
                          <option value={30}>{tr("近 30 天", "Last 30 days")}</option>
                          <option value={90}>{tr("近 90 天", "Last 90 days")}</option>
                        </select>
                        <button disabled={usageBusy} onClick={() => loadDeveloperUsage(usageDays)}>
                          <i className={`fa ${usageBusy ? "fa-spinner detect-spin" : "fa-refresh"}`} /> {tr("刷新用量", "Refresh usage")}
                        </button>
                      </div>
                    </div>
                    {usageStatus && <StatusPill status={usageStatus} />}
                    <div className="token-usage-metrics">
                      <div className="token-usage-metric primary">
                        <span>{tr("总调用次数", "Total calls")}</span>
                        <strong>{formatUsageNumber(totalCalls, lang)}</strong>
                        <small>
                          V1 {formatUsageNumber(v1Calls, lang)} {tr("次", "calls")} / V2 {formatUsageNumber(v2Calls, lang)} {tr("次", "calls")}
                        </small>
                      </div>
                      <div className="token-usage-metric">
                        <span>{tr("Token 总量", "Total tokens")}</span>
                        <strong>{formatUsageNumber(usageSummary?.totalTokens, lang)}</strong>
                        <small>
                          Prompt {formatUsageNumber(usageSummary?.promptTokens, lang)} / Completion {formatUsageNumber(usageSummary?.completionTokens, lang)}
                        </small>
                      </div>
                      <div className="token-usage-metric">
                        <span>{tr("V1 图像模型", "V1 image model")}</span>
                        <strong>{formatUsageNumber(v1Calls, lang)}</strong>
                        <small>{tr("V1 记录调用次数；响应不返回 tokenUsage", "V1 records call count; the response does not return tokenUsage.")}</small>
                      </div>
                      <div className="token-usage-metric">
                        <span>{tr("V2 多模态", "V2 multimodal")}</span>
                        <strong>{formatUsageNumber(v2Calls, lang)}</strong>
                        <small>{tr("缓存命中", "Cache hits")} {formatUsageNumber(usageSummary?.cacheHits, lang)} {tr("次；最近", "times; latest")} {formatUsageDate(usageSummary?.lastEventAt, lang)}</small>
                      </div>
                    </div>
                    <div className="usage-pipeline-grid">
                      {pipelineUsage.map((item) => (
                        <div key={item.pipeline || "unknown"}>
                          <span>{String(item.pipeline || "unknown").toUpperCase()}</span>
                          <strong>{formatUsageNumber(item.requests, lang)} {tr("次", "calls")}</strong>
                          <small>{formatUsageNumber(item.totalTokens, lang)} tokens</small>
                        </div>
                      ))}
                    </div>
                    <div className="token-usage-breakdown">
                      <div className="token-usage-card">
                        <div className="token-usage-card-title">
                          <strong>{tr("最近 7 天趋势", "Last 7 days")}</strong>
                          <span>{tr("按调用次数", "By call count")}</span>
                        </div>
                        <div className="token-usage-bars">
                          {recentUsageDays.map((item) => (
                            <div className="token-usage-bar-row" key={item.date}>
                              <span>{item.date?.slice(5).replace("-", "/")}</span>
                              <div><i style={{ width: `${Math.max(3, (Number(item.requests || 0) / maxDayCalls) * 100)}%` }} /></div>
                              <strong>{formatUsageNumber(item.requests, lang)} {tr("次", "calls")}</strong>
                            </div>
                          ))}
                        </div>
                      </div>
                      <div className="token-usage-card">
                        <div className="token-usage-card-title">
                          <strong>{tr("端点调用", "Endpoint calls")}</strong>
                          <span>Calls / Tokens</span>
                        </div>
                        <div className="token-usage-endpoints">
                          {endpointUsage.length === 0 ? (
                            <p>{tr("暂无调用数据。使用在线测试台或外部智能体调用后会在这里出现。", "No usage data yet. Calls from the online console or external agents will appear here.")}</p>
                          ) : endpointUsage.map((item) => (
                            <div key={`${item.pipeline || "v2"}-${item.endpoint}`}>
                              <code>{item.endpoint}</code>
                              <span>{String(item.pipeline || "v2").toUpperCase()} · {formatUsageNumber(item.requests, lang)} {tr("次", "calls")}</span>
                              <strong>{formatUsageNumber(item.totalTokens, lang)} tokens</strong>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                </>
              )}
            </section>

            <section id="reference" className="docs-section">
              <div className="docs-section-kicker">API Reference</div>
              <h2>{tr("接口总览", "Endpoint index")}</h2>
              <p className="docs-lead">
                V2 {tr("接口基于", "endpoints use")} <code>{REALGUARD_API_BASE}</code>{tr("；", "; ")}V1 {tr("图像模型基于", "image model uses")} <code>{REALGUARD_V1_API_BASE}</code>{tr("。", ". ")}
                {tr("上传接口都使用", "Upload endpoints use")} <code>multipart/form-data</code>{tr("，并传入", " and require")} <code>X-RealGuard-Key</code>{tr("。", ".")}
              </p>
              <div className="endpoint-index">
                {endpoints.map((endpoint) => (
                  <a className="endpoint-index-row" href={endpoint.anchor} key={`${endpoint.method}-${endpoint.path}`}>
                    <span className={`method method-${endpoint.method.toLowerCase()}`}>{endpoint.method}</span>
                    <code>{endpoint.path}</code>
                    <strong>{endpoint.title}</strong>
                    <p>{endpoint.desc}</p>
                  </a>
                ))}
              </div>

              <div id="health" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-get">GET</span>
                  <h3>/health</h3>
                </div>
                <p>{tr("检查 V2 API 可用性、粗粒度能力、上传限制和访问保护状态。公开接口不会返回内部路径或详细阈值。", "Checks V2 API availability, high-level capabilities, upload limits, and access protection. The public endpoint does not return internal paths or detailed thresholds.")}</p>
                <div className="docs-code-block compact"><pre>{curlHealthExample}</pre></div>
              </div>

              <div id="admin-health" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-get">GET</span>
                  <h3>/admin/health</h3>
                </div>
                <p>{tr("受保护的详细诊断接口。启用访问令牌后，需要传入", "Protected detailed diagnostics. When access tokens are enabled, pass")} <code>X-Jianzhen-Token</code>{tr("。", ".")}</p>
                <div className="docs-code-block compact"><pre>{`curl -fsS ${REALGUARD_API_BASE}/admin/health \\
  -H "X-Jianzhen-Token: <token>"`}</pre></div>
              </div>

              <div id="detect" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/detect · V2</h3>
                </div>
                <p>{tr("核心鉴伪接口。上传文件后返回任务编号、鉴伪结论、置信度、证据摘要、模型版本和报告编号。默认上传上限为 25MB。", "Core forensics endpoint. Upload a file to receive task ID, verdict, confidence, evidence summary, model version, and report ID. Default upload limit is 25 MB.")}</p>
                <h4>Request body</h4>
                <div className="docs-table docs-table-4">
                  <strong>{tr("字段", "Field")}</strong><strong>{tr("类型", "Type")}</strong><strong>{tr("是否必填", "Required")}</strong><strong>{tr("说明", "Description")}</strong>
                  {requestParams.map(([name, type, required, desc]) => (
                    <Fragment key={name}>
                      <code>{name}</code><span>{type}</span><span>{required}</span><p>{desc}</p>
                    </Fragment>
                  ))}
                </div>
                <h4>Response fields</h4>
                <div className="docs-table docs-table-2">
                  <strong>{tr("字段", "Field")}</strong><strong>{tr("说明", "Description")}</strong>
                  {fields.map(([field, desc]) => (
                    <Fragment key={field}>
                      <code>{field}</code><p>{desc}</p>
                    </Fragment>
                  ))}
                </div>
                <div className="docs-code-block compact"><pre>{curlDetectExample}</pre></div>
              </div>

              <div id="v1-detect" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/api/developer/v1/detect · V1</h3>
                </div>
                <p>
                  {tr("V1 图像模型接口。适合需要复用旧 RealGuard 图像鉴伪链路的智能体或业务系统。请求使用", "V1 image-model endpoint for agents or business systems that need the legacy RealGuard image-forensics pipeline. Upload an image with the")} <code>file</code>
                  {tr("字段上传图片；平台会记录调用次数，但 V1 响应不返回 tokenUsage。", " field. The platform records call count, but V1 responses do not return tokenUsage.")}
                </p>
                <h4>Response fields</h4>
                <div className="docs-table docs-table-2">
                  <strong>{tr("字段", "Field")}</strong><strong>{tr("说明", "Description")}</strong>
                  {v1Fields.map(([field, desc]) => (
                    <Fragment key={field}>
                      <code>{field}</code><p>{desc}</p>
                    </Fragment>
                  ))}
                </div>
                <div className="docs-code-block compact"><pre>{curlV1DetectExample}</pre></div>
              </div>

              <div id="forensics" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/forensics</h3>
                </div>
                <p>{tr("针对图像返回更细的取证证据，例如 ELA、噪声一致性、频域异常、边缘异常和可解释性摘要。", "Returns deeper image forensics evidence such as ELA, noise consistency, frequency anomalies, edge anomalies, and explainability summaries.")}</p>
                <div className="docs-code-block compact"><pre>{curlForensicsExample}</pre></div>
              </div>

              <div id="provenance" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/provenance</h3>
                </div>
                <p>{tr("验证 C2PA、SynthID、可见水印和内容凭证信号。适合与", "Verifies C2PA, SynthID, visible watermark, and content-credential signals. Combine it with")} <code>/detect</code>{tr("的模型结论合并展示。", " model verdicts for display.")}</p>
                <div className="docs-code-block compact"><pre>{curlProvenanceExample}</pre></div>
              </div>

              <div id="reports" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-get">GET</span>
                  <h3>/report/{"{reportId}"}/download</h3>
                </div>
                <p>{tr("根据检测返回的", "Download a report with the")} <code>reportId</code>{tr("下载报告，用于外部系统归档、审计或人工复核。", " returned by detection for external archiving, auditing, or manual review.")}</p>
                <div className="docs-table docs-table-4">
                  <strong>{tr("路径参数", "Path parameter")}</strong><strong>{tr("类型", "Type")}</strong><strong>{tr("是否必填", "Required")}</strong><strong>{tr("说明", "Description")}</strong>
                  {reportPathParams.map(([name, type, required, desc]) => (
                    <Fragment key={name}>
                      <code>{name}</code><span>{type}</span><span>{required}</span><p>{desc}</p>
                    </Fragment>
                  ))}
                </div>
                <div className="docs-code-block compact"><pre>{curlReportExample}</pre></div>
              </div>
            </section>

            <section id="errors" className="docs-section">
              <div className="docs-section-kicker">Errors</div>
              <h2>{tr("错误码", "Error codes")}</h2>
              <p className="docs-lead">{tr("智能体不应该吞掉 API 错误。4xx 通常是请求或权限问题，5xx 应记录上下文后重试或降级。", "Agents should not hide API errors. 4xx usually means request or permission issues; 5xx should be logged with context before retry or fallback.")}</p>
              <div className="docs-table docs-table-3">
                <strong>HTTP</strong><strong>{tr("名称", "Name")}</strong><strong>{tr("处理方式", "Handling")}</strong>
                {errorRows.map(([code, name, desc]) => (
                  <Fragment key={code}>
                    <code>{code}</code><span>{name}</span><p>{desc}</p>
                  </Fragment>
                ))}
              </div>
            </section>

            <section id="examples" className="docs-section">
              <div className="docs-section-kicker">Examples</div>
              <h2>{tr("代码示例", "Code examples")}</h2>
              <div className="docs-code-grid">
                <div className="docs-code-block">
                  <div className="docs-code-title"><i className="fa fa-jsfiddle" /> JavaScript Fetch</div>
                  <pre>{jsExample}</pre>
                </div>
                <div className="docs-code-block">
                  <div className="docs-code-title"><i className="fa fa-code" /> Python Requests</div>
                  <pre>{pythonExample}</pre>
                </div>
                <div className="docs-code-block">
                  <div className="docs-code-title"><i className="fa fa-terminal" /> RealGuard CLI</div>
                  <pre>{cliExample}</pre>
                </div>
                <div className="docs-code-block">
                  <div className="docs-code-title"><span className="method method-post">POST</span><span>V1 Image Detect</span></div>
                  <pre>{curlV1DetectExample}</pre>
                </div>
              </div>
            </section>

            <section id="console" className="docs-section docs-console-section">
              <div className="docs-section-kicker">API Console</div>
              <h2>{tr("在线 API 测试台", "Online API console")}</h2>
              <p className="docs-lead">{tr("在网站内直接测试健康检查和鉴伪上传，验证 API Key、接口连通性、响应字段和耗时。", "Test health checks and forensic uploads directly on the site to verify API keys, connectivity, response fields, and latency.")}</p>
              <div className="console-layout">
                <div className="console-controls">
                  <label>
                    API Key
                    <input
                      type="password"
                      placeholder="rg_sk_..."
                      value={apiKey}
                      onChange={(event) => setApiKey(event.target.value)}
                    />
                  </label>
                  <label>
                    {tr("文件类型", "File type")}
                    <select value={fileType} onChange={(event) => setFileType(event.target.value)}>
                      <option value="image">image</option>
                      <option value="video">video</option>
                      <option value="audio">audio</option>
                      <option value="document">document</option>
                    </select>
                  </label>
                  <label>
                    {tr("测试文件", "Test file")}
                  <input
                    type="file"
                    accept="image/*,video/*,audio/*,.txt,.pdf,.doc,.docx,.md"
                    onChange={(event) => {
                      const selected = event.target.files?.[0] || null;
                      if (selected) {
                        const validation = validateFile(selected, { kind: tr("测试文件", "test file"), maxBytes: V2_CONSOLE_MAX_BYTES, lang });
                        if (validation) {
                          setConsoleStatus({ tone: "error", text: validation });
                          event.target.value = "";
                          setTestFile(null);
                          return;
                        }
                      }
                      setConsoleStatus(selected ? { tone: "info", text: tr(`已选择：${selected.name}`, `Selected: ${selected.name}`) } : null);
                      setTestFile(selected);
                    }}
                  />
                  </label>
                  <div className="console-actions">
                    <button disabled={consoleBusy} onClick={runHealthCheck}>
                      <i className={`fa ${consoleBusy ? "fa-spinner detect-spin" : "fa-heartbeat"}`} /> {tr("健康检查", "Health check")}
                    </button>
                    <button disabled={consoleBusy || !testFile} onClick={runDetectTest}>
                      <i className={`fa ${consoleBusy ? "fa-spinner detect-spin" : "fa-play"}`} /> {tr("运行鉴伪测试", "Run detection test")}
                    </button>
                  </div>
                  {consoleStatus && <StatusPill status={consoleStatus} />}
                  {consoleMeta && (
                    <div className="console-meta">
                      <span>{consoleMeta.endpoint}</span>
                      <span>{consoleMeta.elapsedMs}ms</span>
                      <span>{consoleMeta.at}</span>
                    </div>
                  )}
                </div>
                <div className="console-result">
                  <div className="console-result-header">
                    <span>{tr("响应 JSON", "Response JSON")}</span>
                    {renderedResult && (
                      <button onClick={() => copyDeveloperText("console-json", renderedResult)}>{copiedDocId === "console-json" ? UI_TEXT[lang].copy.copied : UI_TEXT[lang].copy.copy}</button>
                    )}
                  </div>
                  <pre>{renderedResult || tr("运行健康检查或鉴伪测试后，响应 JSON 会显示在这里。", "Run a health check or detection test and the response JSON will appear here.")}</pre>
                </div>
              </div>
            </section>

            <section id="agent-fields" className="docs-section">
              <div className="docs-section-kicker">Agent Output</div>
              <h2>{tr("智能体应读取的关键字段", "Key fields agents should read")}</h2>
              <p className="docs-lead">{tr("外部智能体输出时至少包含结论、置信度、证据摘要、版本和报告编号，避免只输出一句“真假”。V2 和 V1 的字段结构不同，必须按所选链路解析。", "External agents should output at least verdict, confidence, evidence summary, version, and report ID, not just a one-word true/false answer. V2 and V1 have different field structures and must be parsed according to the selected pipeline.")}</p>
              <h4>{tr("V2 字段", "V2 fields")}</h4>
              <div className="developer-fields">
                {fields.map(([field, desc]) => (
                  <div key={field}>
                    <code>{field}</code>
                    <p>{desc}</p>
                  </div>
                ))}
              </div>
              <h4>{tr("V1 字段", "V1 fields")}</h4>
              <div className="developer-fields">
                {v1Fields.map(([field, desc]) => (
                  <div key={field}>
                    <code>{field}</code>
                    <p>{desc}</p>
                  </div>
                ))}
              </div>
            </section>

            <section id="enterprise" className="docs-section">
              <div className="docs-section-kicker">Enterprise</div>
              <h2>{tr("企业接入标准", "Enterprise integration standards")}</h2>
              <div className="developer-fields">
                <div><code>{tr("版本固定", "Version pinning")}</code><p>{tr("记录", "Record")} <code>modelVersion</code> {tr("与", "and")} <code>cacheVersion</code>{tr("，避免不同分析版本混用。", " to avoid mixing results from different analysis logic.")}</p></div>
                <div><code>{tr("审计留痕", "Audit trail")}</code><p>{tr("保存原始 JSON、", "Save raw JSON, ")}<code>taskId</code>{tr("、", ", ")}<code>reportId</code>{tr("、文件摘要和调用时间。", ", file digest, and call time.")}</p></div>
                <div><code>{tr("错误处理", "Error handling")}</code><p>{tr("对 4xx 展示请求问题，对 5xx 做重试或降级；不要吞掉 API 错误。", "Show request issues for 4xx, retry or degrade for 5xx, and never hide API errors.")}</p></div>
                <div><code>{tr("结论约束", "Verdict constraints")}</code><p>{tr("输出必须包含置信度和限制说明，不得把检测结果表述为绝对证明。", "Output must include confidence and limitations; do not present detection as absolute proof.")}</p></div>
              </div>
            </section>

            <section id="resources" className="docs-section docs-resource-section">
              <div className="docs-section-kicker">Resources</div>
              <h2>{tr("公开资源", "Public resources")}</h2>
              <div className="docs-resource-grid">
                <a href={REALGUARD_SKILL_URL} target="_blank" rel="noreferrer">
                  <span>Agent Skill</span>
                  <code>{REALGUARD_SKILL_URL}</code>
                </a>
                <a href={REALGUARD_API_DOC_URL} target="_blank" rel="noreferrer">
                  <span>Markdown API Docs</span>
                  <code>{REALGUARD_API_DOC_URL}</code>
                </a>
                <a href="/v2/">
                  <span>V2 Agent Console</span>
                  <code>http://124.222.3.205/v2/</code>
                </a>
                <a href="#v1-detect">
                  <span>V1 Image API</span>
                  <code>{REALGUARD_V1_API_BASE}/detect</code>
                </a>
              </div>
              <div className="docs-callout">
                <strong>{tr("解释边界", "Explanation boundary")}</strong>
                <p>
                  {tr("API 结果是鉴伪证据，不是绝对证明。智能体必须说明", "API results are forensic evidence, not absolute proof. Agents must state")} <code>source</code>{tr("、", ", ")}<code>modelVersion</code> {tr("和", "and")}
                  <code>cacheVersion</code>{tr("；若 source 为 mock、heuristic 或回退链路，应明确标注限制。", "; if source is mock, heuristic, or a fallback path, limitations must be clearly marked.")}
                </p>
              </div>
            </section>
          </article>
        </div>
      </div>
    </main>
  );
}

function ExampleCard({ image, title, desc, real, fake, lang }: { image: string; title: string; desc: string; real: number; fake: number; lang: Lang }) {
  return (
    <div className="example-card fade-up visible">
      <div className="example-img">
        <img src={image} alt={title} />
        <span className="example-badge fake">{lang === "zh" ? "生成图像" : "Generated image"}</span>
      </div>
      <div className="example-body">
        <h3>{title}</h3>
        <p>{desc}</p>
        <Progress label={lang === "zh" ? "真实概率" : "Real probability"} value={real} tone="green" />
        <Progress label={lang === "zh" ? "生成概率" : "Generated probability"} value={fake} tone="red" />
      </div>
    </div>
  );
}

function ImageDetectionPage({
  lang,
  isGuest,
  guestDetections,
  onNeedAuth,
  onDone
}: {
  lang: Lang;
  isGuest: boolean;
  guestDetections: number;
  onNeedAuth: () => void;
  onDone: () => Promise<void>;
}) {
  const pageText = UI_TEXT[lang].pages;
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  const imageKind = tr("图片", "image");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [result, setResult] = useState<ImageDetectionResult | null>(null);
  const [status, setStatus] = useState<Status>({ tone: "info", text: tr("等待上传图片...", "Waiting for image upload...") });
  const [busy, setBusy] = useState(false);

  function selectFile(next: File | null) {
    if (next) {
      const message = validateFile(next, { kind: imageKind, maxBytes: IMAGE_MAX_BYTES, mimePrefixes: ["image/"], lang });
      if (message) {
        setStatus({ tone: "error", text: message });
        return;
      }
    }
    setFile(next);
    setResult(null);
    setPreview(next ? URL.createObjectURL(next) : "");
    setStatus({ tone: "info", text: next ? tr(`已选择: ${next.name}`, `Selected: ${next.name}`) : tr("等待上传图片...", "Waiting for image upload...") });
  }

  async function submit() {
    if (!file) {
      setStatus({ tone: "error", text: tr("请先选择图片", "Select an image first") });
      return;
    }
    if (isGuest && guestDetections >= 1) {
      setStatus({ tone: "info", text: tr("访客免费检测次数已用完，请登录后继续检测", "Guest free detection has been used. Log in to continue.") });
      onNeedAuth();
      return;
    }
    setBusy(true);
    setStatus({ tone: "info", text: tr("正在分析图像……", "Analyzing image...") });
    try {
      const data = await detectImage(file);
      setResult(data.result);
      setStatus({ tone: "ok", text: tr("检测完成", "Detection complete") });
      await onDone();
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function detectSample(sample: { image: string; title: string }) {
    if (isGuest && guestDetections >= 1) {
      setStatus({ tone: "info", text: tr("访客免费检测次数已用完，请登录后继续检测", "Guest free detection has been used. Log in to continue.") });
      onNeedAuth();
      return;
    }
    setBusy(true);
    setResult(null);
    setStatus({ tone: "info", text: tr(`正在加载示例图片：${sample.title}`, `Loading sample image: ${sample.title}`) });
    try {
      const response = await fetch(sample.image);
      if (!response.ok) {
        throw new Error(tr(`示例图片加载失败：${response.status}`, `Sample image failed to load: ${response.status}`));
      }
      const blob = await response.blob();
      const ext = sample.image.split(".").pop()?.split("?")[0] || "jpg";
      const sampleFile = new File([blob], `${sample.title}.${ext}`, {
        type: blob.type || "image/jpeg"
      });
      setFile(sampleFile);
      setPreview(URL.createObjectURL(sampleFile));
      setStatus({ tone: "info", text: tr("正在分析示例图片……", "Analyzing sample image...") });
      const data = await detectImage(sampleFile);
      setResult(data.result);
      setStatus({ tone: "ok", text: tr("示例图片检测完成", "Sample image detection complete") });
      await onDone();
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="main">
      <div className="container">
        <PageHeader icon="fa-image" title={pageText.imageTitle} desc={pageText.imageDesc} />
        <div className="layout">
          <div className="card">
            <div className="section-label"><i className="fa fa-cogs" /> {tr("选择检测模型", "Select detection model")}</div>
            <div className="model-tabs">
              <button className="model-tab active"><i className="fa fa-magic" /> {tr("生成内容检测", "AIGC detection")}</button>
              <button className="model-tab"><i className="fa fa-paint-brush" /> {tr("篡改检测", "Tamper detection")}</button>
            </div>
            <div className="model-desc"><strong>{tr("生成内容检测：", "AIGC detection: ")}</strong>{tr("基于检测器快速判定生成概率，并结合元数据做辅助展示。", "Quickly estimates generation probability and uses metadata as supporting context.")}</div>
            <div className="card-divider" />
            <div className="section-label"><i className="fa fa-upload" /> {tr("上传图片", "Upload image")}</div>
            {isGuest && <TrialHint used={guestDetections} lang={lang} />}
            <UploadBox accept="image/*" file={file} preview={preview} onFile={selectFile} kind={imageKind} lang={lang} />
            <button className={`btn-primary ${busy ? "detecting" : ""}`} disabled={!file || busy} onClick={submit}>
              <i className={`fa ${busy ? "fa-circle-o-notch detect-spin" : "fa-search"}`} /> {busy ? tr("正在分析", "Analyzing") : tr("开始检测", "Start detection")}
            </button>
          </div>
          <div className="card">
            <div className="section-label"><i className="fa fa-info-circle" /> {tr("当前状态", "Current status")}</div>
            <StatusRow status={status} busy={busy} />
            <div className="card-divider" />
            {result ? <ImageResult result={result} lang={lang} /> : <ImageSamples onSelect={detectSample} busy={busy} lang={lang} />}
          </div>
        </div>
      </div>
    </main>
  );
}

function VideoDetectionPage({
  lang,
  isGuest,
  guestDetections,
  onNeedAuth,
  onDone
}: {
  lang: Lang;
  isGuest: boolean;
  guestDetections: number;
  onNeedAuth: () => void;
  onDone: () => Promise<void>;
}) {
  const pageText = UI_TEXT[lang].pages;
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  const videoKind = tr("视频", "video");
  const [file, setFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [result, setResult] = useState<VideoDetectionResult | null>(null);
  const [status, setStatus] = useState<Status>({ tone: "info", text: tr("等待上传视频或填写 URL...", "Waiting for video upload or URL...") });
  const [busy, setBusy] = useState(false);

  function selectFile(next: File | null) {
    if (next) {
      const message = validateFile(next, { kind: videoKind, maxBytes: VIDEO_MAX_BYTES, mimePrefixes: ["video/"], lang });
      if (message) {
        setStatus({ tone: "error", text: message });
        return;
      }
    }
    setFile(next);
    setResult(null);
    setStatus({ tone: "info", text: next ? tr(`已选择: ${next.name}`, `Selected: ${next.name}`) : tr("等待上传视频或填写 URL...", "Waiting for video upload or URL...") });
  }

  async function submit() {
    if (!file && !videoUrl.trim()) {
      setStatus({ tone: "error", text: tr("请上传视频或填写视频 URL", "Upload a video or enter a video URL") });
      return;
    }
    if (isGuest && guestDetections >= 1) {
      setStatus({ tone: "info", text: tr("访客免费检测次数已用完，请登录后继续检测", "Guest free detection has been used. Log in to continue.") });
      onNeedAuth();
      return;
    }
    setBusy(true);
    setStatus({ tone: "info", text: tr("正在分析视频帧与编码特征…", "Analyzing video frames and encoding features...") });
    try {
      const data = await detectVideo({ file: file || undefined, videoUrl, fastMode: true });
      setResult(data.result);
      setStatus({ tone: "ok", text: tr("检测完成", "Detection complete") });
      await onDone();
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="main">
      <div className="container">
        <PageHeader icon="fa-film" title={pageText.videoTitle} desc={pageText.videoDesc} />
        <div className="layout">
          <div className="card">
            <div className="section-label"><i className="fa fa-upload" /> {tr("上传视频", "Upload video")}</div>
            {isGuest && <TrialHint used={guestDetections} lang={lang} />}
            <UploadBox accept="video/*" file={file} onFile={selectFile} kind={videoKind} lang={lang} />
            <div className="url-or">{tr("或", "or")}</div>
            <div className="section-label"><i className="fa fa-link" /> {tr("输入视频 URL", "Enter video URL")}</div>
            <div className="url-input-wrap">
              <input className="url-input" value={videoUrl} onChange={(event) => setVideoUrl(event.target.value)} placeholder="https://example.com/video.mp4" />
            </div>
            <button className={`btn-primary ${busy ? "detecting" : ""}`} disabled={busy || (!file && !videoUrl.trim())} onClick={submit}>
              <i className={`fa ${busy ? "fa-spinner detect-spin" : "fa-search"}`} /> {busy ? tr("检测中…", "Detecting...") : tr("开始检测", "Start detection")}
            </button>
          </div>
          <div className="card">
            <div className="section-label"><i className="fa fa-info-circle" /> {tr("当前状态", "Current status")}</div>
            <StatusRow status={status} busy={busy} />
            <div className="card-divider" />
            {result ? <VideoResult result={result} lang={lang} /> : <VideoSamples lang={lang} />}
          </div>
        </div>
      </div>
    </main>
  );
}

function RetrievePage({ onDone, lang }: { onDone: () => Promise<void>; lang: Lang }) {
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  const [searchType, setSearchType] = useState<"image" | "video">("image");
  const [file, setFile] = useState<File | null>(null);
  const [libraries, setLibraries] = useState<string[]>([]);
  const [dataset, setDataset] = useState("");
  const [topK, setTopK] = useState(50);
  const [results, setResults] = useState<RetrieveItem[]>([]);
  const [baseUrl, setBaseUrl] = useState("");
  const [status, setStatus] = useState<Status>({ tone: "info", text: tr("等待上传图片...", "Waiting for image upload...") });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setFile(null);
    setResults([]);
    setBaseUrl("");
    setStatus({ tone: "info", text: searchType === "image" ? tr("等待上传图片...", "Waiting for image upload...") : tr("等待上传视频...", "Waiting for video upload...") });
    getLibraries(searchType)
      .then((data) => {
        setLibraries(data.libraries || []);
        setDataset(data.selected || data.libraries?.[0] || "");
      })
      .catch((error) => setStatus({ tone: "error", text: errorMessage(error) }));
  }, [searchType]);

  function selectFile(next: File | null) {
    if (next) {
      const message = validateFile(next, {
        kind: searchType === "image" ? tr("图片", "image") : tr("视频", "video"),
        maxBytes: searchType === "image" ? IMAGE_MAX_BYTES : VIDEO_MAX_BYTES,
        mimePrefixes: [searchType === "image" ? "image/" : "video/"],
        lang,
      });
      if (message) {
        setStatus({ tone: "error", text: message });
        return;
      }
    }
    setFile(next);
    setResults([]);
    setStatus({ tone: "info", text: next ? tr(`已选择: ${next.name}`, `Selected: ${next.name}`) : tr("等待上传查询文件...", "Waiting for query file...") });
  }

  async function submit() {
    if (!file) {
      setStatus({ tone: "error", text: tr("请先上传查询文件", "Upload a query file first") });
      return;
    }
    setBusy(true);
    setStatus({ tone: "info", text: tr("正在检索可疑内容...", "Searching suspicious content...") });
    try {
      const data = await retrieveSearch({ file, searchType, dataset, topK });
      setResults(data.results || []);
      setBaseUrl(data.base_url || "");
      setStatus({ tone: "ok", text: tr(`检索完成，共 ${data.results?.length || 0} 条可疑结果`, `Search complete, ${data.results?.length || 0} suspicious results`) });
      await onDone();
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="main">
      <div className="container">
        <PageHeader icon="fa-search" title={searchType === "image" ? UI_TEXT[lang].pages.retrieveImageTitle : UI_TEXT[lang].pages.retrieveVideoTitle} desc={UI_TEXT[lang].pages.retrieveDesc} />
        <div className="layout">
          <div className="card">
            <div className="section-label"><i className="fa fa-upload" /> {tr("文件上传", "File upload")}</div>
            <UploadBox accept={searchType === "image" ? "image/*" : "video/*"} file={file} onFile={selectFile} kind={searchType === "image" ? tr("图片", "image") : tr("视频", "video")} lang={lang} />
            <button className="btn-primary" disabled={!file || busy} onClick={submit}><i className="fa fa-search" /> {busy ? tr("检索中...", "Searching...") : tr("开始检索", "Start retrieval")}</button>
          </div>
          <div className="card">
            <div className="section-label"><i className="fa fa-sliders" /> {tr("检索参数", "Retrieval parameters")}</div>
            <label className="param-label">{tr("检索类型", "Retrieval type")}</label>
            <select className="param-select" value={searchType} onChange={(event) => setSearchType(event.target.value as "image" | "video")}>
              <option value="image">{UI_TEXT[lang].pages.retrieveImageTitle}</option>
              <option value="video">{UI_TEXT[lang].pages.retrieveVideoTitle}</option>
            </select>
            <label className="param-label">{tr("检索库", "Library")}</label>
            <select className="param-select" value={dataset} onChange={(event) => setDataset(event.target.value)}>
              {libraries.length ? libraries.map((item) => <option key={item} value={item}>{item}</option>) : <option value="">{tr("无可用检索库", "No available libraries")}</option>}
            </select>
            <label className="param-label">{tr("返回数量", "Result count")}</label>
            <select className="param-select" value={topK} onChange={(event) => setTopK(Number(event.target.value))}>
              <option value={5}>Top 5</option>
              <option value={10}>Top 10</option>
              <option value={20}>Top 20</option>
              <option value={50}>Top 50</option>
            </select>
            <div className="status-box">
              <div className="status-label"><i className="fa fa-info-circle" /> {tr("状态信息", "Status")}</div>
              <div className="status-text">{status?.text}</div>
            </div>
          </div>
        </div>
        {results.length > 0 && <RetrieveResults results={results} baseUrl={baseUrl} searchType={searchType} lang={lang} />}
      </div>
    </main>
  );
}

function HistoryPage({ setPage, lang }: { setPage: (page: PageKey) => void; lang: Lang }) {
  const [tab, setTab] = useState<HistoryTabKey>(() => getInitialHistoryTab());
  const [records, setRecords] = useState<HistoryRecord[]>([]);
  const [status, setStatus] = useState<Status>(null);
  const [filter, setFilter] = useState<HistoryFilterKey>(() => getInitialHistoryFilter(getInitialHistoryTab()));
  const [query, setQuery] = useState(() => getInitialHistoryQuery());
  const [copied, setCopied] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyLimit, setHistoryLimit] = useState(HISTORY_PAGE_SIZE);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyFilterCounts, setHistoryFilterCounts] = useState<Partial<Record<HistoryFilterKey, number>>>({});
  const [debouncedQuery, setDebouncedQuery] = useState(() => getInitialHistoryQuery());
  const historyRequestIdRef = useRef(0);
  const tr = (zh: string, en: string) => translate(lang, zh, en);

  async function loadHistoryRecords(
    targetTab: HistoryTabKey,
    { preserveOnError = false, append = false, reset = false }: { preserveOnError?: boolean; append?: boolean; reset?: boolean } = {},
  ) {
    const requestId = historyRequestIdRef.current + 1;
    historyRequestIdRef.current = requestId;
    setStatus({ tone: "info", text: tr("正在加载历史记录", "Loading history records") });
    setHistoryBusy(true);
    const activeFilter = isHistoryFilterSupported(targetTab, filter) ? filter : "all";
    const offset = append ? records.length : 0;
    const limit = append ? HISTORY_PAGE_SIZE : reset ? HISTORY_PAGE_SIZE : historyLimit;
    const request =
      targetTab === "image"
        ? getHistory("image-detections", { query: debouncedQuery, filter: activeFilter, limit, offset })
        : targetTab === "video"
          ? getHistory("video-detections", { query: debouncedQuery, filter: activeFilter, limit, offset })
          : getRetrievalHistory(targetTab === "imageRetrieve" ? "image" : "video", { query: debouncedQuery, filter: activeFilter, limit, offset });
    try {
      const data: HistoryListResponse = await request;
      if (historyRequestIdRef.current !== requestId) return;
      if (append) {
        setRecords((current) => {
          const seen = new Set(current.map((record) => String(record.itemid || "")));
          return current.concat((data.records || []).filter((record) => !seen.has(String(record.itemid || ""))));
        });
      } else {
        setRecords(data.records || []);
      }
      setHistoryTotal(Number(data.total || 0));
      setHistoryFilterCounts(data.filter_counts || {});
      setStatus(null);
    } catch (error) {
      if (historyRequestIdRef.current !== requestId) return;
      if (!preserveOnError) {
        setRecords([]);
        setHistoryTotal(0);
        setHistoryFilterCounts({});
      }
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      if (historyRequestIdRef.current === requestId) {
        setHistoryBusy(false);
      }
    }
  }

  useEffect(() => {
    if (!isHistoryFilterSupported(tab, filter)) return;
    void loadHistoryRecords(tab, { reset: true });
  }, [tab, filter, debouncedQuery]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!isHistoryFilterSupported(tab, filter)) {
      setFilter("all");
    }
  }, [tab]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    params.set("page", "history");
    params.set("historyTab", tab);
    const activeFilter = isHistoryFilterSupported(tab, filter) ? filter : "all";
    if (activeFilter !== "all") params.set("historyFilter", activeFilter);
    else params.delete("historyFilter");
    if (query.trim()) params.set("historyQuery", query.trim());
    else params.delete("historyQuery");
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}`);
  }, [tab, filter, query]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);

  function updateHistoryTab(nextTab: HistoryTabKey) {
    setHistoryLimit(HISTORY_PAGE_SIZE);
    setTab(nextTab);
  }

  function updateHistoryFilter(nextFilter: HistoryFilterKey) {
    setHistoryLimit(HISTORY_PAGE_SIZE);
    setFilter(nextFilter);
  }

  function updateHistoryQuery(nextQuery: string) {
    setHistoryLimit(HISTORY_PAGE_SIZE);
    setQuery(nextQuery);
  }

  const summaryCards = useMemo<HistorySummaryCard[]>(() => {
    if (tab === "image") {
      return [
        { label: tr("当前记录", "Current records"), value: historyFilterCounts.all ?? historyTotal, filterKey: "all" as HistoryFilterKey },
        { label: tr("访客记录", "Guest records"), value: historyFilterCounts.guest ?? 0, filterKey: "guest" as HistoryFilterKey },
        { label: tr("带元数据", "With metadata"), value: historyFilterCounts.metadata ?? 0, filterKey: "metadata" as HistoryFilterKey },
        { label: tr("有可疑点", "With issues"), value: historyFilterCounts.issues ?? 0, filterKey: "issues" as HistoryFilterKey },
      ];
    }
    if (tab === "video") {
      return [
        { label: tr("当前记录", "Current records"), value: historyFilterCounts.all ?? historyTotal, filterKey: "all" as HistoryFilterKey },
        { label: tr("访客记录", "Guest records"), value: historyFilterCounts.guest ?? 0, filterKey: "guest" as HistoryFilterKey },
        { label: tr("生成结论", "AI verdicts"), value: historyFilterCounts.ai ?? 0, filterKey: "ai" as HistoryFilterKey },
        { label: tr("真实结论", "Real verdicts"), value: historyFilterCounts.real ?? 0, filterKey: "real" as HistoryFilterKey },
      ];
    }
    const resultCount = records.reduce((sum, record) => sum + Number(record.result_count || 0), 0);
    const topKAvg = records.length
      ? Math.round((records.reduce((sum, record) => sum + Number(record.top_k || 0), 0) / records.length) * 10) / 10
      : 0;
    return [
      { label: tr("当前查询", "Current query"), value: historyTotal || records.length },
      { label: tr("有命中", "With hits"), value: historyFilterCounts.hits ?? 0, filterKey: "hits" as HistoryFilterKey },
      { label: tr("无命中", "No hits"), value: historyFilterCounts.empty ?? 0, filterKey: "empty" as HistoryFilterKey },
      { label: tr("命中总数", "Total hits"), value: resultCount },
      { label: tr("平均 Top-K", "Average Top-K"), value: topKAvg },
      { label: tr("查询类型", "Query type"), value: tab === "imageRetrieve" ? tr("图像", "Image") : tr("视频", "Video") },
    ];
  }, [historyFilterCounts, historyTotal, records, tab, lang]);

  const filterOptions = getHistoryFilterOptions(tab, lang);
  const activeSummary = getHistoryActiveSummary(tab, filter, query, lang);
  const matchSummary =
    records.length === historyTotal
      ? tr(`当前展示 ${records.length} 条记录`, `Showing ${records.length} records`)
      : tr(`当前匹配 ${records.length} / ${historyTotal} 条记录`, `Matched ${records.length} / ${historyTotal} records`);

  async function copyCurrentView() {
    const url = window.location.href;
    await copyTextToClipboard(url, tr("复制当前历史视图链接", "Copy current history view URL"));
    setCopied(true);
  }

  return (
    <main className="main">
      <div className="container">
        <PageHeader icon="fa-history" title={UI_TEXT[lang].pages.historyTitle} desc={UI_TEXT[lang].pages.historyDesc} />
        <div className="card">
          <div className="model-tabs history-tabs">
            <button className={`model-tab ${tab === "image" ? "active" : ""}`} onClick={() => updateHistoryTab("image")}>{UI_TEXT[lang].pages.imageTitle}</button>
            <button className={`model-tab ${tab === "video" ? "active" : ""}`} onClick={() => updateHistoryTab("video")}>{UI_TEXT[lang].pages.videoTitle}</button>
            <button className={`model-tab ${tab === "imageRetrieve" ? "active" : ""}`} onClick={() => updateHistoryTab("imageRetrieve")}>{tr("图像检索", "Image retrieval")}</button>
            <button className={`model-tab ${tab === "videoRetrieve" ? "active" : ""}`} onClick={() => updateHistoryTab("videoRetrieve")}>{tr("视频检索", "Video retrieval")}</button>
          </div>
          {status && <div className={`notice ${status.tone}`}>{status.text}</div>}
          {records.length ? (
            <>
              <div className="history-summary-grid">
                {summaryCards.map((card) => (
                  <button
                    key={card.label}
                    type="button"
                    className={`history-summary-card ${card.filterKey === filter ? "active" : ""}`}
                    onClick={() => {
                      if (!card.filterKey) return;
                      updateHistoryFilter(card.filterKey === filter ? "all" : card.filterKey);
                    }}
                  >
                    <span>{card.label}</span>
                    <strong>{card.value}</strong>
                  </button>
                ))}
              </div>
              <div className="history-search-bar">
                <div className="input-wrap">
                  <i className="fa fa-search" />
                  <input
                    value={query}
                    onChange={(event) => updateHistoryQuery(event.target.value)}
                    placeholder={tab === "imageRetrieve" || tab === "videoRetrieve" ? tr("按文件名、命中库、首个命中、时间搜索历史记录", "Search by filename, library, first hit, or time") : tr("按文件名、结论、时间搜索历史记录", "Search by filename, verdict, or time")}
                  />
                </div>
                {query && (
                  <button type="button" className="btn-code history-search-clear" onClick={() => updateHistoryQuery("")}>
                    {tr("清空", "Clear")}
                  </button>
                )}
              </div>
              <div className="history-active-bar">
                <div className="history-active-tags">
                  {activeSummary.map((item) => (
                    <span key={item.label} className="history-active-tag">
                      <strong>{item.label}</strong>
                      <span>{item.value}</span>
                    </span>
                  ))}
                </div>
                <div className="history-active-meta">{matchSummary}</div>
                <button
                  type="button"
                  className="btn-code history-refresh-btn"
                  onClick={() => {
                    void loadHistoryRecords(tab, { preserveOnError: true });
                  }}
                  disabled={historyBusy}
                >
                  {historyBusy ? tr("刷新中", "Refreshing") : tr("刷新记录", "Refresh records")}
                </button>
                <button
                  type="button"
                  className={`btn-code history-copy-btn ${
                    copied ? "history-copy-btn-copied" : ""
                  }`}
                  onClick={copyCurrentView}
                >
                  {copied ? tr("已复制视图链接", "View URL copied") : tr("复制当前视图", "Copy current view")}
                </button>
                {(filter !== "all" || query.trim()) && (
                  <button
                  type="button"
                  className="btn-code history-reset-btn"
                  onClick={() => {
                    updateHistoryFilter("all");
                    updateHistoryQuery("");
                  }}
                >
                  {tr("重置条件", "Reset filters")}
                  </button>
                )}
              </div>
              {filterOptions.length > 0 && (
                <div className="history-filter-bar">
                  {filterOptions.map((option) => (
                    <button
                      key={option.key}
                      type="button"
                      className={`history-filter-btn ${filter === option.key ? "active" : ""}`}
                      onClick={() => updateHistoryFilter(option.key)}
                    >
                      <span>{option.label}</span>
                      <span className="history-filter-count">{historyFilterCounts[option.key] ?? 0}</span>
                    </button>
                  ))}
                </div>
              )}
              {records.length ? (
                <>
                  <HistoryRecords records={records} tab={tab} query={query} lang={lang} />
                  {records.length < historyTotal && (
                    <div className="history-load-more">
                      <button
                        type="button"
                        className="btn-code history-load-more-btn"
                        disabled={historyBusy}
                        onClick={() => {
                          setHistoryLimit((value) => value + HISTORY_PAGE_SIZE);
                          void loadHistoryRecords(tab, { preserveOnError: true, append: true });
                        }}
                      >
                        {historyBusy ? tr("加载中", "Loading") : tr("加载更多", "Load more")}
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <EmptyState
                  icon="fa-filter"
                  text={tr("当前筛选条件下暂无记录", "No records match the current filters")}
                  actions={[
                    { label: tr("清除条件", "Clear filters"), onClick: () => { updateHistoryFilter("all"); updateHistoryQuery(""); } },
                    { label: tab === "video" ? UI_TEXT[lang].pages.videoTitle : tab === "image" ? UI_TEXT[lang].pages.imageTitle : tr("去侵权检索", "Go to retrieval"), onClick: () => setPage(tab === "video" ? "video" : tab === "image" ? "image" : "retrieve") },
                  ]}
                />
              )}
            </>
          ) : status?.tone === "error" ? (
            <EmptyState
              icon="fa-exclamation-triangle"
              text={status.text}
              actions={[
                { label: historyBusy ? tr("加载中", "Loading") : tr("重试加载", "Retry"), onClick: () => { void loadHistoryRecords(tab); } },
                { label: tab === "video" ? UI_TEXT[lang].pages.videoTitle : tab === "image" ? UI_TEXT[lang].pages.imageTitle : tr("去侵权检索", "Go to retrieval"), onClick: () => setPage(tab === "video" ? "video" : tab === "image" ? "image" : "retrieve") },
              ]}
            />
          ) : !status && (filter !== "all" || query.trim()) ? (
            <EmptyState
              icon="fa-filter"
              text={tr("当前筛选条件下暂无记录", "No records match the current filters")}
              actions={[
                { label: tr("清除条件", "Clear filters"), onClick: () => { updateHistoryFilter("all"); updateHistoryQuery(""); } },
                { label: tab === "video" ? UI_TEXT[lang].pages.videoTitle : tab === "image" ? UI_TEXT[lang].pages.imageTitle : tr("去侵权检索", "Go to retrieval"), onClick: () => setPage(tab === "video" ? "video" : tab === "image" ? "image" : "retrieve") },
              ]}
            />
          ) : !status && (
            <EmptyState
              icon="fa-clock-o"
              text={tr("暂无记录", "No records yet")}
              actions={[
                { label: UI_TEXT[lang].pages.imageTitle, onClick: () => setPage("image") },
                { label: UI_TEXT[lang].pages.videoTitle, onClick: () => setPage("video") },
                { label: tr("去侵权检索", "Go to retrieval"), onClick: () => setPage("retrieve") },
              ]}
            />
          )}
        </div>
      </div>
    </main>
  );
}

function PageHeader({ icon, title, desc }: { icon: string; title: string; desc: string }) {
  return (
    <div className="page-header">
      <h1><i className={`fa ${icon}`} /> {title}</h1>
      <p>{desc}</p>
    </div>
  );
}

function TrialHint({ used, lang }: { used: number; lang: Lang }) {
  return (
    <div className="trial-note">
      <i className="fa fa-info-circle" />
      <span>
        {used >= 1
          ? translate(lang, "访客检测次数已用完，登录后继续使用。", "Guest detection has been used. Log in to continue.")
          : translate(lang, "访客可免费完成 1 次检测，本次不会要求登录。", "Guests can complete one free detection. This run does not require login.")}
      </span>
    </div>
  );
}

function StatusPill({ status }: { status: Status }) {
  if (!status) return null;
  return (
    <div className={`status-pill ${status.tone}`}>
      <i className={`fa ${status.tone === "ok" ? "fa-check-circle" : status.tone === "error" ? "fa-exclamation-circle" : "fa-info-circle"}`} />
      <span>{status.text}</span>
    </div>
  );
}

function UploadBox({
  accept,
  file,
  preview,
  onFile,
  kind,
  lang
}: {
  accept: string;
  file: File | null;
  preview?: string;
  onFile: (file: File | null) => void;
  kind: string;
  lang: Lang;
}) {
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <div className="upload-area">
      <input accept={accept} type="file" id={`file-${kind}`} onChange={(event) => onFile(event.target.files?.[0] || null)} />
      {!file ? (
        <label htmlFor={`file-${kind}`} className="upload-placeholder">
          <div className="upload-icon"><i className="fa fa-cloud-upload" /></div>
          <div className="upload-text">{tr(`拖放${kind}到此处，或点击上传`, `Drop ${kind} here, or click to upload`)}</div>
          <div className="upload-hint">{tr(`支持常见${kind}格式`, `Supports common ${kind} formats`)}</div>
        </label>
      ) : (
        <div className="file-preview visible">
          {preview && <img src={preview} alt={tr("预览", "Preview")} />}
          <div className="file-meta">
            <span>{file.name}</span><span>·</span><span>{formatSize(file.size)}</span><span className="file-badge">{kind}</span>
          </div>
          <button className="clear-btn" onClick={() => onFile(null)}><i className="fa fa-times" /> {tr("清除", "Clear")}</button>
        </div>
      )}
    </div>
  );
}

function StatusRow({ status, busy }: { status: Status; busy: boolean }) {
  return (
    <div className="status-row">
      <div className={`status-dot ${status?.tone === "ok" ? "ready" : ""} ${busy ? "busy" : ""}`} />
      <div className="status-text">{status?.text}</div>
    </div>
  );
}

function ImageSamples({
  onSelect,
  busy,
  lang
}: {
  onSelect: (sample: { image: string; title: string }) => void;
  busy: boolean;
  lang: Lang;
}) {
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <>
      <div className="section-label"><i className="fa fa-th-large" style={{ color: "var(--warning)" }} /> {tr("示例图片", "Sample images")} <span className="label-muted">{tr("点击直接检测", "Click to detect")}</span></div>
      <div className="sample-list">
        <SampleItem image="/system/index1.jpg" title={tr("示例图片 1", "Sample image 1")} label={tr("点击检测", "Detect")} neutral disabled={busy} onClick={onSelect} lang={lang} />
        <SampleItem image="/system/index2.jpg" title={tr("示例图片 2", "Sample image 2")} label={tr("点击检测", "Detect")} neutral disabled={busy} onClick={onSelect} lang={lang} />
        <SampleItem image="/system/index3.jpg" title={tr("示例图片 3", "Sample image 3")} label={tr("点击检测", "Detect")} neutral disabled={busy} onClick={onSelect} lang={lang} />
      </div>
      <div className="card-divider" />
      <Tips lang={lang} items={[
        tr("生成内容检测：识别 SD、DALL-E、Midjourney 等模型生成图像", "AIGC detection: identifies images generated by SD, DALL-E, Midjourney, and similar models"),
        tr("篡改检测：识别拼接、修补、克隆等篡改痕迹", "Tamper detection: identifies splicing, inpainting, cloning, and related traces"),
        tr("结果包含概率、置信度与简洁结论", "Results include probability, confidence, and a concise verdict"),
      ]} />
    </>
  );
}

function VideoSamples({ lang }: { lang: Lang }) {
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <>
      <div className="section-label"><i className="fa fa-th-large" style={{ color: "var(--warning)" }} /> {tr("示例视频", "Sample videos")} <span className="label-muted">{tr("点击查看效果", "Click to preview")}</span></div>
      <div className="sample-list">
        <SampleItem image="/system/video5227-cover.jpg" title={tr("示例视频 1（video5227）", "Sample video 1 (video5227)")} label={tr("示例", "Sample")} fake play lang={lang} />
        <SampleItem image="/system/video189-cover.jpg" title={tr("示例视频 2（video189）", "Sample video 2 (video189)")} label={tr("示例", "Sample")} play lang={lang} />
        <SampleItem image="/system/video6785-cover.jpg" title={tr("示例视频 3（video6785）", "Sample video 3 (video6785)")} label={tr("示例", "Sample")} fake play lang={lang} />
      </div>
      <div className="card-divider" />
      <Tips lang={lang} items={[
        tr("支持本地文件上传和远程 URL 两种方式", "Supports both local upload and remote URL input"),
        tr("若文件和 URL 同时存在，优先使用本地文件", "If both file and URL are present, local file takes priority"),
        tr("检测结果包含生成/真实概率、置信度和说明", "Results include generated/real probabilities, confidence, and explanation"),
      ]} />
    </>
  );
}

function SampleItem({
  image,
  title,
  label,
  fake,
  neutral,
  play,
  disabled,
  onClick,
  lang
}: {
  image: string;
  title: string;
  label: string;
  fake?: boolean;
  neutral?: boolean;
  play?: boolean;
  disabled?: boolean;
  onClick?: (sample: { image: string; title: string }) => void;
  lang: Lang;
}) {
  const labelClass = neutral ? "neutral" : fake ? "fake" : "real";
  const labelIcon = neutral ? "fa-search" : fake ? "fa-times" : "fa-check";

  return (
    <button className="sample-item" type="button" disabled={disabled} onClick={() => onClick?.({ image, title })}>
      <div className="sample-thumb">
        <img src={image} alt={title} />
        {play && <i className="fa fa-play-circle play-icon" />}
      </div>
      <div className="sample-body">
        <div className="sample-name">{title}</div>
        <div className="sample-meta">
          <span className={`sample-label ${labelClass}`}><i className={`fa ${labelIcon}`} /> {label}</span>
          <span className="sample-hint">{translate(lang, "查看", "View")} <i className="fa fa-chevron-right" /></span>
        </div>
      </div>
    </button>
  );
}

function Tips({ items, lang }: { items: string[]; lang: Lang }) {
  return (
    <>
      <div className="section-label"><i className="fa fa-lightbulb-o" style={{ color: "var(--warning)" }} /> {translate(lang, "使用说明", "Usage notes")}</div>
      <ul className="tips-list">
        {items.map((item) => <li key={item}><i className="fa fa-check-circle" /><span>{item}</span></li>)}
      </ul>
    </>
  );
}

function ImageResult({ result, lang }: { result: ImageDetectionResult; lang: Lang }) {
  const probability = Math.round((result.probability || 0) * 1000) / 10;
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <div className="result-panel">
      <div className="section-label"><i className="fa fa-bar-chart" /> {tr("检测结果", "Detection result")}</div>
      {result.image_url && <img className="result-media" src={result.image_url} alt={result.filename} />}
      <div className="verdict-row">
        <span className={result.final_label.includes("AI") ? "pill danger" : "pill ok"}>{result.final_label}</span>
        <strong>{probability}%</strong>
      </div>
      <div className="case-kv">
        <Info label={tr("置信度", "Confidence")} value={result.confidence || "-"} />
        <Info label={tr("文件名", "Filename")} value={result.filename || "-"} />
        <Info label={tr("格式", "Format")} value={result.img_format || "-"} />
        <Info label={tr("分辨率", "Resolution")} value={result.resolution || "-"} />
      </div>
      <div className="result-actions">
        <button className="btn-code" type="button" onClick={() => downloadImageReport(result.itemid)}>
          <i className="fa fa-download" /> {tr("下载报告", "Download report")}
        </button>
      </div>
      <div className="case-block"><p>{result.explanation}</p></div>
    </div>
  );
}

function VideoResult({ result, lang }: { result: VideoDetectionResult; lang: Lang }) {
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <div className="result-panel">
      <div className="section-label"><i className="fa fa-bar-chart" /> {tr("视频检测结果", "Video detection result")}</div>
      {result.video_url && <video className="result-media" src={result.video_url} controls />}
      <div className="verdict-row">
        <span className={result.final_label.includes("AI") ? "pill danger" : "pill ok"}>{result.final_label || tr("未标注", "Unlabeled")}</span>
        <strong>{Math.round(result.fake_percentage * 10) / 10}%</strong>
      </div>
      <Progress label={tr("真实概率", "Real probability")} value={result.real_percentage} tone="green" />
      <Progress label={tr("生成概率", "Generated probability")} value={result.fake_percentage} tone="red" />
      <div className="result-actions">
        <button className="btn-code" type="button" onClick={() => downloadVideoReport(result.itemid)}>
          <i className="fa fa-download" /> {tr("下载报告", "Download report")}
        </button>
      </div>
      <div className="case-block"><p>{result.explanation || tr("暂无详细说明", "No detailed explanation yet")}</p></div>
    </div>
  );
}

function Progress({ label, value, tone }: { label: string; value: number; tone: "green" | "red" }) {
  return (
    <div className="bar-group">
      <div className="bar-label"><span>{label}</span><span className="val">{value}%</span></div>
      <div className="bar-track"><div className={`bar-fill ${tone}`} style={{ width: `${value}%` }} /></div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="case-kv-item">
      <div className="k">{label}</div>
      <div className="v">{value}</div>
    </div>
  );
}

function RetrieveResults({ results, baseUrl, searchType, lang }: { results: RetrieveItem[]; baseUrl: string; searchType: "image" | "video"; lang: Lang }) {
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <div className="results-section visible">
      <div className="card result-card-wrap">
        <div className="results-header">
          <h2><i className="fa fa-bar-chart" /> {tr("侵权检索结果", "Retrieval results")}</h2>
          <div className="results-summary">{tr(`共 ${results.length} 条可疑结果`, `${results.length} suspicious results`)}</div>
        </div>
        <div className="results-grid">
          {results.slice(0, 15).map((item) => {
            const mediaPath = item.product?.product_images || item.id;
            const src = `${baseUrl}${encodeURI(mediaPath)}`;
            return (
              <div className="result-card" key={`${item.rank}-${item.id}`}>
                <div className="result-thumb">
                  {searchType === "image" ? <img src={src} alt={tr(`第 ${item.rank} 名`, `Rank ${item.rank}`)} /> : <video src={src} />}
                  <div className={`rank-badge ${item.rank <= 3 ? `rank-${item.rank}` : "rank-default"}`}>{item.rank}</div>
                </div>
                <div className="result-body">
                  <div className="result-name">{item.id}</div>
                  <div className="result-score-row"><span className="result-score-label">{tr("相似度", "Similarity")}</span><span className="result-score-val">{Number(item.score || 0).toFixed(3)}</span></div>
                  <div className="score-bar"><div className="score-bar-inner" style={{ width: `${Math.max(0, Math.min(1, item.score || 0)) * 100}%` }} /></div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function HistoryRecords({
  records,
  tab,
  query,
  lang,
}: {
  records: HistoryRecord[];
  tab: "image" | "video" | "imageRetrieve" | "videoRetrieve";
  query: string;
  lang: Lang;
}) {
  const isVideo = tab === "video" || tab === "videoRetrieve";
  const isRetrieval = tab === "imageRetrieve" || tab === "videoRetrieve";
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <div className="history-grid">
      {records.map((record, index) => {
        const mediaUrl = historyMediaUrl(record);
        const previewUrl = historyPreviewUrl(record) || mediaUrl;
        const title = String(record.filename || tr(`历史记录 ${index + 1}`, `History record ${index + 1}`));
        const resultCount = Number(record.result_count || 0);
        const verdict = isRetrieval
          ? tr(`${resultCount} 条结果`, `${resultCount} results`)
          : String(record.final_label || "-");
        const meta = isRetrieval
          ? String(record.top_k || "-")
          : String(record.confidence || "-");
        const reportUrl = String(record.report_url || "");
        const guestRecord = Boolean(record.is_guest_record);
        const hasMetadata = Boolean(record.has_metadata);
        const hasIssues = Boolean(record.has_visual_issues);
        const issueCount = Number(record.visual_issue_count || 0);
        const timeText = String(record.createtime || "-");
        const retrievalTag = tab === "imageRetrieve" ? tr("图像检索", "Image retrieval") : tab === "videoRetrieve" ? tr("视频检索", "Video retrieval") : "";
        const hasHits = resultCount > 0;
        const topResultId = String(record.top_result_id || "");
        const topResultLibrary = String(record.top_result_library || "");
        const topResultScore = Number(record.top_result_score || 0);
        return (
          <article className="history-record" key={`${record.itemid || index}`}>
            <a className="history-media" href={mediaUrl || undefined} target={mediaUrl ? "_blank" : undefined} rel="noreferrer" aria-label={mediaUrl ? tr(`查看 ${title}`, `View ${title}`) : title}>
              {previewUrl ? (
                isVideo ? (
                  <div className="history-placeholder"><i className="fa fa-film" /></div>
                ) : (
                  <img src={previewUrl} alt={title} loading="lazy" />
                )
              ) : (
                <div className="history-placeholder"><i className={`fa ${isVideo ? "fa-film" : "fa-image"}`} /></div>
              )}
              {mediaUrl && <span className="history-view"><i className="fa fa-eye" /> {tr("查看", "View")}</span>}
            </a>
            <div className="history-body">
              <div className="history-title" title={title}>{renderHighlightedText(title, query)}</div>
              {guestRecord && (
                <div className="history-tags">
                  <span className="history-tag guest"><i className="fa fa-user-secret" /> {renderHighlightedText(tr("访客", "Guest"), query)}</span>
                  {hasMetadata && <span className="history-tag meta"><i className="fa fa-info-circle" /> {renderHighlightedText(tr("元数据", "Metadata"), query)}</span>}
                  {hasIssues && <span className="history-tag issue"><i className="fa fa-exclamation-triangle" /> {renderHighlightedText(issueCount > 0 ? tr(`可疑点 ${issueCount}`, `Issues ${issueCount}`) : tr("可疑点", "Issues"), query)}</span>}
                </div>
              )}
              {!guestRecord && (hasMetadata || hasIssues) && (
                <div className="history-tags">
                  {hasMetadata && <span className="history-tag meta"><i className="fa fa-info-circle" /> {renderHighlightedText(tr("元数据", "Metadata"), query)}</span>}
                  {hasIssues && <span className="history-tag issue"><i className="fa fa-exclamation-triangle" /> {renderHighlightedText(issueCount > 0 ? tr(`可疑点 ${issueCount}`, `Issues ${issueCount}`) : tr("可疑点", "Issues"), query)}</span>}
                </div>
              )}
              {isRetrieval ? (
                <div className="history-tags">
                  {retrievalTag && <span className="history-tag meta"><i className="fa fa-search" /> {renderHighlightedText(retrievalTag, query)}</span>}
                  <span className={`history-tag ${hasHits ? "meta" : "issue"}`}>
                    <i className={`fa ${hasHits ? "fa-check-circle" : "fa-minus-circle"}`} />
                    {renderHighlightedText(hasHits ? tr("有命中", "With hits") : tr("无命中", "No hits"), query)}
                  </span>
                </div>
              ) : null}
              <div className="history-row"><span>{tr("时间", "Time")}</span><strong>{renderHighlightedText(timeText, query)}</strong></div>
              <div className="history-row"><span>{isRetrieval ? tr("数量", "Count") : tr("结论", "Verdict")}</span><strong>{renderHighlightedText(verdict, query)}</strong></div>
              <div className="history-row"><span>{isRetrieval ? "Top-K" : tr("置信度", "Confidence")}</span><strong>{renderHighlightedText(meta, query)}</strong></div>
              {isRetrieval && topResultLibrary && (
                <div className="history-row"><span>{tr("命中库", "Hit library")}</span><strong>{renderHighlightedText(topResultLibrary, query)}</strong></div>
              )}
              {isRetrieval && topResultId && (
                <>
                  <div className="history-row"><span>{tr("首个命中", "First hit")}</span><strong>{renderHighlightedText(topResultId, query)}</strong></div>
                  <div className="history-row"><span>{tr("最高分", "Top score")}</span><strong>{renderHighlightedText(topResultScore.toFixed(4), query)}</strong></div>
                </>
              )}
              {!isRetrieval && reportUrl && (
                <div className="history-actions">
                  <button
                    className="btn-code history-action-btn"
                    type="button"
                    onClick={() => {
                      if (tab === "image") downloadImageReport(Number(record.itemid));
                      else if (tab === "video") downloadVideoReport(Number(record.itemid));
                    }}
                  >
                    <i className="fa fa-download" /> {tr("报告", "Report")}
                  </button>
                </div>
              )}
              {isRetrieval && reportUrl && (
                <div className="history-actions">
                  <button
                    className="btn-code history-action-btn"
                    type="button"
                    onClick={() => downloadRetrieveReport(Number(record.itemid))}
                  >
                    <i className="fa fa-download" /> {tr("报告", "Report")}
                  </button>
                </div>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function historyMediaUrl(record: HistoryRecord) {
  return String(record.image_url || record.video_url || record.file_url || "");
}

function historyPreviewUrl(record: HistoryRecord) {
  return String(record.thumbnail_url || "");
}

function EmptyState({
  icon,
  text,
  actions = [],
}: {
  icon: string;
  text: string;
  actions?: Array<{ label: string; onClick: () => void }>;
}) {
  return (
    <div className="empty-state">
      <i className={`fa ${icon}`} />
      <span>{text}</span>
      {actions.length > 0 && (
        <div className="empty-state-actions">
          {actions.map((action) => (
            <button key={action.label} type="button" className="btn-code empty-state-btn" onClick={action.onClick}>
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AuthModal({ onAuthed, onClose, lang }: { onAuthed: () => Promise<void>; onClose: () => void; lang: Lang }) {
  const text = UI_TEXT[lang].auth;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="login-card modal-login-card">
        <button className="case-modal-close modal-close" onClick={onClose}><i className="fa fa-times" /></button>
        <div className="login-header">
          <span className="login-icon"><i className="fa fa-shield" /></span>
          <div>
            <h2>{text.title}</h2>
            <p className="sub">{text.desc}</p>
          </div>
        </div>
        <AuthForm onAuthed={onAuthed} lang={lang} />
      </div>
    </div>
  );
}

function AuthForm({ onAuthed, lang }: { onAuthed: () => Promise<void>; lang: Lang }) {
  const [mode, setMode] = useState<AuthMode>("password");
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [username, setUsername] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [status, setStatus] = useState<Status>(null);
  const [busy, setBusy] = useState(false);
  const [codeBusy, setCodeBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const text = UI_TEXT[lang].auth;
  const tr = (zh: string, en: string) => translate(lang, zh, en);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  async function sendCode(scene: "login" | "register") {
    setStatus(null);
    if (!/^1[3-9]\d{9}$/.test(phone.trim())) {
      setStatus({ tone: "error", text: tr("请输入正确的手机号", "Enter a valid phone number") });
      return;
    }
    setCodeBusy(true);
    try {
      const data = await sendSmsCode(phone, scene);
      if (data.debug_code && import.meta.env.DEV) {
        setSmsCode(data.debug_code);
        setStatus({ tone: "ok", text: tr(`开发模式已自动填入验证码：${data.debug_code}`, `Development mode filled the code automatically: ${data.debug_code}`) });
      } else {
        setStatus({ tone: "ok", text: tr("验证码已发送，请查看短信", "Code sent. Check your SMS.") });
      }
      setCooldown(data.expires_in ? 60 : 45);
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setCodeBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!/^1[3-9]\d{9}$/.test(phone.trim())) {
      setStatus({ tone: "error", text: tr("请输入正确的手机号", "Enter a valid phone number") });
      return;
    }
    if ((mode === "sms" || mode === "register") && !smsCode.trim()) {
      setStatus({ tone: "error", text: tr("请输入短信验证码", "Enter the SMS code") });
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      if (mode === "password") await loginByPassword(phone, secret);
      else if (mode === "sms") await loginBySms(phone, smsCode);
      else {
        if (!secret.trim()) {
          setStatus({ tone: "error", text: tr("请设置登录密码", "Set a login password") });
          return;
        }
        await registerUser({ phone, secret, username, sms_code: smsCode });
        setStatus({ tone: "ok", text: tr("注册成功，请切换到登录", "Account created. Switch to log in.") });
        setMode("password");
        return;
      }
      await onAuthed();
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="login-tabs">
        <button type="button" className={`login-tab ${mode === "password" ? "active" : ""}`} onClick={() => setMode("password")}>{text.password}</button>
        <button type="button" className={`login-tab ${mode === "sms" ? "active" : ""}`} onClick={() => setMode("sms")}>{text.sms}</button>
        <button type="button" className={`login-tab ${mode === "register" ? "active" : ""}`} onClick={() => setMode("register")}>{text.register}</button>
      </div>
      <form onSubmit={submit} className="login-panel active">
        <AuthInput icon="fa-phone" label={text.phone} value={phone} onChange={setPhone} placeholder={text.phonePlaceholder} />
        {mode === "register" && <AuthInput icon="fa-user" label={text.username} value={username} onChange={setUsername} placeholder={text.usernamePlaceholder} />}
        {(mode === "password" || mode === "register") && <AuthInput icon="fa-lock" label={text.passwordLabel} value={secret} onChange={setSecret} placeholder={text.passwordPlaceholder} type="password" />}
        {(mode === "sms" || mode === "register") && (
          <div className="form-group">
            <label className="form-label">{text.smsCode}</label>
            <div className="code-row">
              <div className="input-wrap">
                <i className="fa fa-shield" />
                <input value={smsCode} onChange={(event) => setSmsCode(event.target.value)} placeholder={text.smsPlaceholder} />
              </div>
              <button
                type="button"
                className="btn-code"
                disabled={codeBusy || cooldown > 0}
                onClick={() => sendCode(mode === "register" ? "register" : "login")}
              >
                {codeBusy ? text.sending : cooldown > 0 ? `${cooldown}s` : text.sendCode}
              </button>
            </div>
          </div>
        )}
        {status && <div className={`notice ${status.tone}`}>{status.text}</div>}
        <button type="submit" className="btn-primary" disabled={busy}><i className="fa fa-sign-in" /> {mode === "register" ? text.create : text.login}</button>
      </form>
    </>
  );
}

function AuthInput({ icon, label, value, onChange, placeholder, type = "text" }: { icon: string; label: string; value: string; onChange: (value: string) => void; placeholder: string; type?: string }) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <div className="input-wrap">
        <i className={`fa ${icon}`} />
        <input type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
      </div>
    </div>
  );
}

function Footer({ lang }: { lang: Lang }) {
  const text = UI_TEXT[lang].footer;
  return (
    <footer className="footer">
      <div className="footer-logo"><i className="fa fa-eye" /> {text.brand}</div>
      <p className="footer-copy">{text.copy}</p>
    </footer>
  );
}

function formatSize(size: number) {
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

function validateFile(
  file: File,
  options: { kind: string; maxBytes: number; mimePrefixes?: string[]; lang?: Lang },
) {
  const lang = options.lang || "zh";
  if (file.size > options.maxBytes) {
    return translate(
      lang,
      `${options.kind}不能超过 ${formatSize(options.maxBytes)}，当前文件为 ${formatSize(file.size)}。`,
      `The ${options.kind} must be smaller than ${formatSize(options.maxBytes)}. Current size: ${formatSize(file.size)}.`
    );
  }
  if (options.mimePrefixes?.length && file.type) {
    const allowed = options.mimePrefixes.some((prefix) => file.type.startsWith(prefix));
    if (!allowed) {
      return translate(
        lang,
        `请选择${options.kind}文件，当前文件类型为 ${file.type}。`,
        `Select a valid ${options.kind}. Current file type: ${file.type}.`
      );
    }
  }
  return "";
}

function colorBg(color: string) {
  if (color === "var(--primary)") return "rgba(11,92,255,0.12)";
  if (color === "var(--primary-light)") return "rgba(79,156,255,0.16)";
  if (color === "var(--primary-dark)") return "rgba(18,17,15,0.12)";
  if (color === "var(--warning)") return "rgba(245,159,0,0.16)";
  if (color === "var(--accent)") return "rgba(183,255,42,0.24)";
  return "rgba(18,17,15,0.08)";
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作失败";
}

function isDemoMode() {
  return new URLSearchParams(window.location.search).get("demo") === "1";
}

function useDeviceType() {
  const [deviceType, setDeviceType] = useState<"mobile" | "desktop">(() =>
    typeof window !== "undefined" && window.matchMedia("(max-width: 768px)").matches ? "mobile" : "desktop"
  );

  useEffect(() => {
    const media = window.matchMedia("(max-width: 768px)");
    const update = () => setDeviceType(media.matches ? "mobile" : "desktop");
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return deviceType;
}

function getGuestDetections() {
  const storage = getStorage();
  const raw = storage?.getItem("realguard_guest_detections") || "0";
  const parsed = Number(raw || "0");
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function setGuestDetectionsStorage(value: number) {
  getStorage()?.setItem("realguard_guest_detections", String(Math.max(0, value)));
}

function getInitialLang(): Lang {
  if (typeof window === "undefined") return "zh";
  const value = getStorage()?.getItem("realguard_lang");
  return value === "en" ? "en" : "zh";
}

function getInitialPage(): PageKey {
  if (typeof window === "undefined") return "home";
  const value = new URLSearchParams(window.location.search).get("page") as PageKey | null;
  return value && ["home", "image", "video", "retrieve", "history", "developer"].includes(value) ? value : "home";
}

function getInitialHistoryTab(): HistoryTabKey {
  if (typeof window === "undefined") return "image";
  const value = new URLSearchParams(window.location.search).get("historyTab") as HistoryTabKey | null;
  return value && ["image", "video", "imageRetrieve", "videoRetrieve"].includes(value) ? value : "image";
}

function getInitialHistoryFilter(tab: HistoryTabKey): HistoryFilterKey {
  if (typeof window === "undefined") return "all";
  const value = new URLSearchParams(window.location.search).get("historyFilter") as HistoryFilterKey | null;
  return value && isHistoryFilterSupported(tab, value) ? value : "all";
}

function getInitialHistoryQuery() {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("historyQuery") || "";
}

function renderHighlightedText(text: string, query: string) {
  const trimmed = query.trim();
  if (!trimmed) return text;
  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(${escaped})`, "ig");
  const lower = trimmed.toLowerCase();
  const parts = text.split(pattern);
  return parts.map((part, index) =>
    part.toLowerCase() === lower ? (
      <mark key={`${part}-${index}`} className="history-highlight">{part}</mark>
    ) : (
      <span key={`${part}-${index}`}>{part}</span>
    ),
  );
}

function getSearchableHistoryFields(record: HistoryRecord) {
  const issueCount = Number(record.visual_issue_count || 0);
  const resultCount = Number(record.result_count || 0);
  const searchType = String(record.search_type || "");
  const isRetrievalRecord = searchType === "image" || searchType === "video";
  const topResultId = String(record.top_result_id || "");
  const topResultLibrary = String(record.top_result_library || "");
  const topResultScore = Number(record.top_result_score || 0);
  return [
    String(record.filename || ""),
    String(record.final_label || ""),
    String(record.confidence || ""),
    String(record.createtime || ""),
    String(record.top_k || ""),
    String(resultCount || ""),
    searchType,
    searchType === "image" ? "图像检索" : searchType === "video" ? "视频检索" : "",
    resultCount > 0 ? "有命中" : "无命中",
    topResultId,
    topResultLibrary,
    topResultLibrary ? `命中库 ${topResultLibrary}` : "",
    topResultLibrary ? `检索库 ${topResultLibrary}` : "",
    topResultId ? topResultScore.toFixed(4) : "",
    Boolean(record.is_guest_record) ? "访客" : "",
    Boolean(record.has_metadata) ? "元数据" : "",
    Boolean(record.has_visual_issues) ? `可疑点${issueCount > 0 ? ` ${issueCount}` : ""}` : "",
    isRetrievalRecord ? "数量" : "结论",
    isRetrievalRecord ? "Top-K" : "置信度",
    topResultId ? "首个命中" : "",
    topResultLibrary ? "命中库" : "",
    topResultId ? "最高分" : "",
  ].map((field) => String(field));
}

function getHistoryFilterOptions(tab: HistoryTabKey, lang: Lang = "zh"): Array<{ key: HistoryFilterKey; label: string }> {
  if (tab === "image") {
    return [
      { key: "all", label: translate(lang, "全部", "All") },
      { key: "guest", label: translate(lang, "访客", "Guest") },
      { key: "metadata", label: translate(lang, "元数据", "Metadata") },
      { key: "issues", label: translate(lang, "可疑点", "Issues") },
    ];
  }
  if (tab === "video") {
    return [
      { key: "all", label: translate(lang, "全部", "All") },
      { key: "guest", label: translate(lang, "访客", "Guest") },
      { key: "ai", label: translate(lang, "生成结论", "AI verdicts") },
      { key: "real", label: translate(lang, "真实结论", "Real verdicts") },
    ];
  }
  return [
    { key: "all", label: translate(lang, "全部", "All") },
    { key: "hits", label: translate(lang, "有命中", "With hits") },
    { key: "empty", label: translate(lang, "无命中", "No hits") },
  ];
}

function matchesHistoryFilter(record: HistoryRecord, tab: HistoryTabKey, filter: HistoryFilterKey) {
  if (filter === "all") return true;
  if (tab === "image") {
    if (filter === "guest") return Boolean(record.is_guest_record);
    if (filter === "metadata") return Boolean(record.has_metadata);
    if (filter === "issues") return Boolean(record.has_visual_issues);
    return true;
  }
  if (tab === "video") {
    if (filter === "guest") return Boolean(record.is_guest_record);
    if (filter === "ai") return String(record.final_label || "").includes("AI");
    if (filter === "real") return String(record.final_label || "").includes("真实");
    return true;
  }
  if (filter === "hits") return Number(record.result_count || 0) > 0;
  if (filter === "empty") return Number(record.result_count || 0) <= 0;
  return true;
}

function isHistoryFilterSupported(tab: HistoryTabKey, filter: HistoryFilterKey) {
  return getHistoryFilterOptions(tab).some((option) => option.key === filter) || filter === "all";
}

function getHistoryActiveSummary(tab: HistoryTabKey, filter: HistoryFilterKey, query: string, lang: Lang = "zh") {
  const tabLabels: Record<HistoryTabKey, string> = {
    image: translate(lang, "图像鉴伪", "Image forensics"),
    video: translate(lang, "视频鉴伪", "Video forensics"),
    imageRetrieve: translate(lang, "图像检索", "Image retrieval"),
    videoRetrieve: translate(lang, "视频检索", "Video retrieval"),
  };
  const filterLabel =
    getHistoryFilterOptions(tab, lang).find((option) => option.key === filter)?.label || translate(lang, "全部", "All");
  return [
    { label: translate(lang, "模块", "Module"), value: tabLabels[tab] },
    { label: translate(lang, "筛选", "Filter"), value: filterLabel },
    { label: translate(lang, "搜索", "Search"), value: query.trim() || translate(lang, "未设置", "Not set") },
  ];
}

function getStorage() {
  try {
    return typeof window.localStorage === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export default App;
