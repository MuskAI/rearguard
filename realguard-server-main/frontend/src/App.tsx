import { DragEvent, FormEvent, InputHTMLAttributes, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  FileArchive,
  FileText,
  Film,
  History as HistoryIcon,
  Image as ImageIcon,
  Layers3,
  Network,
  ShieldCheck,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  Counters,
  DetectionJob,
  HistoryFilterKey,
  HistoryListResponse,
  HistoryRecord,
  ImageDetectionResult,
  PublicExpertReviewExpert,
  User,
  VideoDetectionResult,
  detectImage,
  detectVideo,
  downloadImageReport,
  downloadVideoReport,
  getHistory,
  getImageDetectionJob,
  getMe,
  loginByPassword,
  loginBySms,
  logout,
  registerUser,
  resetPassword,
  sendSmsCode,
  startExpertReviewImageDetection
} from "./api";
import {
  normalizeExpertReviewStatus,
  publicExpertReviewExpertMessage,
  publicExpertReviewExpertName,
  publicExpertReviewExpertStatusLabel,
  publicExpertReviewEvidence,
  publicExpertReviewJobSummary,
} from "./swarmPublic";

type PageKey = "home" | "image" | "video" | "history" | "developer";
type Status = { tone: "ok" | "error" | "info"; text: string } | null;
type AuthMode = "password" | "sms" | "register" | "reset";
type HistoryTabKey = "image" | "video";
type HistorySummaryCard = { label: string; value: number | string; filterKey?: HistoryFilterKey };
type Lang = "zh" | "en";
type ImageDetectMode = "standard" | "swarm";
type IconTone = "blue" | "green" | "amber" | "red" | "ink";

const emptyCounters: Counters = {
  image_detect: 0,
  video_detect: 0
};
const HISTORY_PAGE_SIZE = 100;
const REALGUARD_V2_CONSOLE_URL = "/v2/";
const REALGUARD_TERMS_VERSION = "2026-06-03";
const SWARM_CANCELLED_ERROR = "__REALGUARD_SWARM_CANCELLED__";
const SWARM_RAIN_DROPS = Array.from({ length: 28 }, (_, index) => index);
const SWARM_PLACEHOLDER_EXPERTS: PublicExpertReviewExpert[] = Array.from({ length: 8 }, (_, index) => ({
  id: `placeholder-${index}`,
  status: "queued"
}));
const UI_TEXT = {
  zh: {
    boot: "正在连接系统...",
    nav: {
      brand: "数字内容鉴伪平台",
      brandMobile: "鉴伪平台",
      home: "首页",
      functions: "功能",
      detection: "检测",
      imageDetect: "图像鉴伪",
      videoDetect: "视频鉴伪",
      history: "历史记录",
      developer: "管理入口",
      v2: "深度分析",
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
        history: "历史",
        developer: "管理",
      },
    },
    trial: {
      title: "访客体验",
      desc: "首次检测无需登录，第二次检测前请登录。",
      action: "登录/注册",
    },
    home: {
      eyebrow: "鉴伪情报平台",
      eyebrowNote: "面向内容审核、复核与归档",
      titleLine1: "数字内容",
      titleLine2: "鉴伪工作台",
      desc: "面向图像、视频和深度证据分析的鉴伪工作台。先选择检测类型，再查看证据、报告和历史记录。",
      taskKicker: "任务入口",
      taskTitle: "选择要开始的检测类型",
      primaryAction: "图像鉴伪",
      videoAction: "视频鉴伪",
      secondaryAction: "进入深度分析",
      continueKicker: "继续工作",
      historyAction: "历史记录",
      reportsAction: "报告归档",
      trust1: "图像、视频与深度分析分流",
      trust2: "已处理任务",
      trust3: "登录后查看报告归档",
      briefingLabel: "实时证据简报",
      overall: "综合判断",
      risk: "生成风险 73.9%",
      support: "辅助证据",
      texture: "纹理与边缘异常",
      ela: "压缩误差图",
      elaSmall: "异常区域定位",
      noise: "噪声残差",
      noiseSmall: "生成纹理比对",
      usage: "报告",
      usageSmall: "复核与归档",
      handoff: "报告归档",
      workflowKicker: "从任务开始",
      workflowTitle: "任务先行，再进入证据链。",
      workflowDesc: "普通用户从图像、视频或深度分析进入；复核、历史和报告作为后续工作流承接检测结果。",
      capabilitiesTitle: "核心能力",
      capabilitiesDesc: "检测、报告与深度分析保持独立，但在首页以同一条证据链呈现。",
      evidenceTitle: "结果不是一句话，而是一组可复核证据",
      evidenceDesc: "示例卡保留判断比例，但首页更强调报告、证据字段和后续追踪。",
    },
    workflow: [
      ["内容审核团队", "上传图像或视频，直接获得真伪结论、置信度、证据字段和报告编号。", "开始鉴伪"],
      ["专家会诊复核", "调度多类鉴伪专家投票复核，把共识度、分歧点和证据一起呈现。", "进入会诊"],
      ["历史与报告", "按账号查看检测记录、报告编号和证据摘要，便于复核、归档和追踪。", "查看历史"],
      ["深度分析工作台", "融合误差图、噪声残差和来源凭证，补充更细的取证证据。", "进入分析"],
    ],
    features: [
      ["图像鉴伪", "基于深度学习的图像真伪识别，支持多种场景的生成图像检测。"],
      ["视频鉴伪", "针对视频内容的生成检测与篡改识别，帧级分析定位可疑片段。"],
      ["深度证据分析", "独立分析工作台，融合误差图、噪声残差等取证证据。"],
      ["专家会诊复核", "调度多类鉴伪专家投票复核，输出综合结论、共识度和分歧提示。"],
    ],
    examples: [
      ["案例一：泳池场景人物图像", "综合判断为生成图像（53.8%），点击查看检测结果。"],
      ["案例二：几何色块人像图像", "综合判断为生成图像（73.9%），点击查看检测结果。"],
    ],
    skillPanel: {
      badge1: "检测记录已接入",
      badge2: "报告归档可查看",
      title: "登录后查看检测记录、报告归档和管理入口",
      desc: "普通账号可在这里回到检测结果、报告编号、复核状态和管理入口，不展示技术接入说明。",
      reasonTitle: "为什么需要登录",
      reason: "检测记录、报告归档和管理入口需要绑定账号，便于追踪结果、保护上传内容并保留复核上下文。",
      protocol: [
        ["01 登录", "确认账号", "绑定检测记录与报告"],
        ["02 查看", "选择记录", "回看图像、视频与深度分析结果"],
        ["03 管理", "归档报告", "保留复核状态和追踪信息"],
      ],
      terminalLabel: "推荐工作入口",
      terminalStrong: "登录后继续查看记录",
      copyV2Title: "深度分析入口",
      copyV2Desc: "继续补充更细的取证证据和报告信息。",
      copyUrlTitle: "管理入口",
      copyUrlDesc: "查看账号下的记录与归档信息。",
      copyV1Title: "图像鉴伪入口",
      copyV1Desc: "回到标准图像检测任务。",
      openV2: "进入深度分析",
      openDev: "打开管理入口",
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
      historyTitle: "历史记录",
      historyDesc: "查看检测历史记录。",
    },
    developer: {
      docsBrandSmall: "账号管理",
      badges: ["检测记录", "报告归档", "复核状态", "管理入口"],
      title: "RealGuard 管理入口",
      desc: "账号登录后查看检测记录、报告归档、复核状态和管理入口。这里面向普通用户继续工作，不展示技术接入内容。",
      commands: ["查看记录", "打开报告", "管理归档", "继续检测"],
      keyAction: "查看账号",
      skillAction: "查看报告",
      skillsCopy: "报告归档",
      skillsCopyTitle: "登录后查看检测报告",
      workflow: [
        ["查看检测记录", "登录后按账号查看图像、视频和深度分析任务。"],
        ["打开报告归档", "回看报告编号、证据摘要和下载状态。"],
        ["管理复核状态", "跟踪待复核、已确认和需要补充材料的记录。"],
        ["继续开始任务", "从管理入口回到图像、视频或深度分析工作。"],
      ],
      navGroups: ["继续工作", "记录归档", "账号管理", "资源"],
      navLinks: {
        overview: "总览",
        quickstart: "开始任务",
        skillCopy: "报告归档",
        auth: "账号登录",
        apiKeys: "账号权限",
        tokenUsage: "使用概览",
        reference: "记录总览",
        detect: "多模态分析",
        v1Detect: "标准图像检测",
        forensics: "取证分析",
        provenance: "来源验证",
        reports: "报告",
        errors: "错误码",
        examples: "示例记录",
        console: "管理台",
        agentFields: "记录字段",
        enterprise: "组织管理",
        resources: "相关入口",
      },
    },
    auth: {
      title: "账户登录",
      desc: "登录后 30 天内自动保持状态",
      railTitle: "安全接入",
      railDesc: "一次注册即可使用检测、历史记录、报告归档和管理入口。",
      railPoints: ["协议可追溯", "短信校验", "记录归属账号"],
      password: "密码登录",
      sms: "验证码登录",
      register: "注册",
      reset: "找回密码",
      phone: "手机号",
      username: "用户名",
      passwordLabel: "密码",
      newPasswordLabel: "新密码",
      smsCode: "短信验证码",
      phonePlaceholder: "请输入手机号",
      usernamePlaceholder: "请输入用户名",
      passwordPlaceholder: "请输入密码",
      newPasswordPlaceholder: "至少 8 位，包含字母和数字",
      smsPlaceholder: "请输入验证码",
      sendCode: "获取验证码",
      sending: "发送中",
      create: "创建账号",
      resetAction: "重置密码",
      login: "登录",
      forgot: "忘记密码？",
      backLogin: "返回登录",
      passwordHint: "密码至少 8 位，并同时包含字母和数字。",
      termsPrefix: "我已阅读并同意",
      terms: "用户协议",
      privacy: "隐私政策",
      termsJoin: "和",
      termsRequired: "请先阅读并同意用户协议和隐私政策",
    },
    footer: {
      brand: "数字内容鉴伪平台",
      copy: "© 2026 数字内容鉴伪平台",
      icp: "浙ICP备2026051442号",
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
      imageDetect: "Image Forensics",
      videoDetect: "Video Forensics",
      history: "History",
      developer: "Manage",
      v2: "Deep analysis",
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
        history: "History",
        developer: "Manage",
      },
    },
    trial: {
      title: "Guest access",
      desc: "Your first detection is free. Please log in before the second one.",
      action: "Log in / Sign up",
    },
    home: {
      eyebrow: "Forensic intelligence platform",
      eyebrowNote: "Review and archive ready",
      titleLine1: "Digital content",
      titleLine2: "forensics desk",
      desc: "A forensics workspace for image, video, and deep evidence analysis. Choose a detection type first, then review evidence, reports, and history.",
      taskKicker: "Task entry",
      taskTitle: "Choose the detection type to start",
      primaryAction: "Image forensics",
      videoAction: "Video forensics",
      secondaryAction: "Open deep analysis",
      continueKicker: "Continue work",
      historyAction: "History",
      reportsAction: "Report archive",
      trust1: "Image, video, and deep analysis routing",
      trust2: "Tasks processed",
      trust3: "Reports after login",
      briefingLabel: "Live evidence brief",
      overall: "Overall verdict",
      risk: "AI risk 73.9%",
      support: "Supporting evidence",
      texture: "Texture and edge anomalies",
      ela: "ELA map",
      elaSmall: "Suspicious region localization",
      noise: "Noise residual",
      noiseSmall: "Generated texture comparison",
      usage: "Reports",
      usageSmall: "Review and archive",
      handoff: "Report archive",
      workflowKicker: "Start from the task",
      workflowTitle: "Start with the task, then continue into evidence work.",
      workflowDesc: "Most users begin with image, video, or deep analysis. Review, history, and reports pick up after the result exists.",
      capabilitiesTitle: "Core capabilities",
      capabilitiesDesc: "Detection, reports, and deep analysis stay separate but are presented as one evidence workflow.",
      evidenceTitle: "A result is not a sentence. It is a reviewable evidence set.",
      evidenceDesc: "Examples keep the probability view while emphasizing reports, evidence fields, and follow-up tracking.",
    },
    workflow: [
      ["Content review teams", "Upload images or videos and receive verdicts, confidence, evidence fields, and report IDs.", "Start forensics"],
      ["Expert review", "Run a multi-expert review panel and inspect consensus, disagreements, and supporting evidence.", "Open expert review"],
      ["History and reports", "Review detection records, report IDs, and evidence summaries for audit, archive, and follow-up.", "Open history"],
      ["Deep analysis workbench", "Combine ELA, noise residuals, and provenance signals for deeper forensic evidence.", "Open analysis"],
    ],
    features: [
      ["Image Forensics", "Deep-learning image authenticity detection across generated-image scenarios."],
      ["Video Forensics", "AI-generation and tamper detection for videos with frame-level suspicious segment analysis."],
      ["Deep Evidence Analysis", "A dedicated analysis workbench combining ELA, noise residuals, and other forensic evidence."],
      ["Expert Review Panel", "Runs multiple forensic experts as a voting panel with consensus and disagreement signals."],
    ],
    examples: [
      ["Case 1: Poolside person image", "Overall verdict: likely AI-generated (53.8%). Open the detection result."],
      ["Case 2: Geometric portrait image", "Overall verdict: likely AI-generated (73.9%). Open the detection result."],
    ],
    skillPanel: {
      badge1: "Detection records ready",
      badge2: "Reports available after login",
      title: "Log in to view detection records, report archive, and management entry",
      desc: "Your account keeps results, report IDs, review status, and management entries together for ordinary user workflows.",
      reasonTitle: "Why login is required",
      reason: "Detection records, report archives, and management entries are tied to your account so uploaded content and review context stay protected.",
      protocol: [
        ["01 Login", "Confirm account", "Bind records and reports"],
        ["02 Review", "Choose a record", "Open image, video, or deep analysis results"],
        ["03 Manage", "Archive reports", "Keep review status and trace context"],
      ],
      terminalLabel: "Recommended work entry",
      terminalStrong: "Log in to continue with records",
      copyV2Title: "Deep analysis entry",
      copyV2Desc: "Continue with richer forensic evidence and report information.",
      copyUrlTitle: "Management entry",
      copyUrlDesc: "Review account records and archive information.",
      copyV1Title: "Image forensics entry",
      copyV1Desc: "Return to a standard image detection task.",
      openV2: "Open deep analysis",
      openDev: "Open management",
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
      historyTitle: "History",
      historyDesc: "Review detection records.",
    },
    developer: {
      docsBrandSmall: "Account management",
      badges: ["Detection records", "Report archive", "Review status", "Management"],
      title: "RealGuard Management",
      desc: "After account login, review detection records, report archives, review status, and management entries for ordinary user workflows.",
      commands: ["Review records", "Open reports", "Manage archive", "Continue detection"],
      keyAction: "View account",
      skillAction: "View reports",
      skillsCopy: "Report archive",
      skillsCopyTitle: "Open reports after login",
      workflow: [
        ["Review detection records", "Open image, video, and deep analysis tasks tied to the account."],
        ["Open report archive", "Review report IDs, evidence summaries, and download status."],
        ["Manage review status", "Track pending, confirmed, and needs-more-context records."],
        ["Continue a task", "Return to image, video, or deep analysis work from management."],
      ],
      navGroups: ["Continue", "Records", "Account", "Resources"],
      navLinks: {
        overview: "Overview",
        quickstart: "Start task",
        skillCopy: "Report archive",
        auth: "Account login",
        apiKeys: "Account permissions",
        tokenUsage: "Activity summary",
        reference: "Record index",
        detect: "Multimodal Detect",
        v1Detect: "Standard Image Detection",
        forensics: "Forensics",
        provenance: "Provenance",
        reports: "Reports",
        errors: "Errors",
        examples: "Sample Records",
        console: "Management Console",
        agentFields: "Record Fields",
        enterprise: "Organization",
        resources: "Related Links",
      },
    },
    auth: {
      title: "Account login",
      desc: "Stay signed in for 30 days",
      railTitle: "Secure access",
      railDesc: "One account unlocks forensics, history, report archives, and management.",
      railPoints: ["Auditable consent", "SMS verification", "Records tied to account"],
      password: "Password",
      sms: "SMS code",
      register: "Sign up",
      reset: "Reset password",
      phone: "Phone",
      username: "Username",
      passwordLabel: "Password",
      newPasswordLabel: "New password",
      smsCode: "SMS code",
      phonePlaceholder: "Enter phone number",
      usernamePlaceholder: "Enter username",
      passwordPlaceholder: "Enter password",
      newPasswordPlaceholder: "At least 8 chars with letters and numbers",
      smsPlaceholder: "Enter code",
      sendCode: "Send code",
      sending: "Sending",
      create: "Create account",
      resetAction: "Reset password",
      login: "Log in",
      forgot: "Forgot password?",
      backLogin: "Back to login",
      passwordHint: "Use at least 8 characters with both letters and numbers.",
      termsPrefix: "I have read and agree to the",
      terms: "Terms",
      privacy: "Privacy Policy",
      termsJoin: "and",
      termsRequired: "Please agree to the Terms and Privacy Policy first",
    },
    footer: {
      brand: "Digital Content Forensics",
      copy: "© 2026 Digital Content Forensics Platform",
      icp: "浙ICP备2026051442号",
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
  const [imageModeIntent, setImageModeIntent] = useState<ImageDetectMode>(() => getInitialImageMode());
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
    if (page === "image" && imageModeIntent === "swarm") params.set("imageMode", "swarm");
    else params.delete("imageMode");
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}`);
  }, [page, imageModeIntent]);

  useEffect(() => {
    if (isDemoMode()) {
      setUser({ Userid: 1, username: "演示用户", phone: "13800000000", openid: "demo" });
      setCounters({ image_detect: 18, video_detect: 7 });
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

  function openImage(mode: ImageDetectMode = "standard") {
    setImageModeIntent(mode);
    setPage("image");
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
        openImage={openImage}
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

      {page === "home" && <HomePage counters={counters} setPage={setPage} openImage={openImage} lang={lang} />}
      {page === "image" && (
        <ImageDetectionPage
          lang={lang}
          initialMode={imageModeIntent}
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
      {page === "history" && <HistoryPage setPage={setPage} lang={lang} />}
      {page === "developer" && (
        user ? (
          <AccessPlatformPage onNeedAuth={requireAuth} lang={lang} />
        ) : (
          <AccessLoginGate lang={lang} onLogin={requireAuth} />
        )
      )}

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
  openImage,
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
  openImage: (mode?: ImageDetectMode) => void;
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
          <button className="nav-logo" aria-label={text.brand} onClick={() => go("home")}>
            <i className="fa fa-eye" aria-hidden="true" />
            <span className="logo-full" aria-hidden="true">{text.brand}</span>
            <span className="logo-mobile" aria-hidden="true">{text.brandMobile}</span>
          </button>
          <nav className="nav-links">
            <button className={page === "home" ? "active" : ""} onClick={() => go("home")}>
              {text.home}
            </button>
            <div className="dropdown">
              <button className={`dropdown-trigger ${["image", "video"].includes(page) ? "active" : ""}`}>
                <span>{text.functions}</span>
                <i className="fa fa-chevron-down" />
              </button>
              <div className="dropdown-menu">
                <div className="dropdown-label">{text.detection}</div>
                <button className={`dropdown-item ${page === "image" ? "active-item" : ""}`} onClick={() => { openImage("standard"); setMobileOpen(false); }}>
                  <i className="fa fa-image" /> {text.imageDetect}
                </button>
                <button className={`dropdown-item ${page === "video" ? "active-item" : ""}`} onClick={() => go("video")}>
                  <i className="fa fa-film" /> {text.videoDetect}
                </button>
              </div>
            </div>
            <button className={page === "history" ? "active" : ""} onClick={() => go("history")}>
              {text.history}
            </button>
            <button onClick={() => { window.location.href = REALGUARD_V2_CONSOLE_URL; }}>{text.v2}</button>
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
          <button className={page === "image" ? "active" : ""} onClick={() => { openImage("standard"); setMobileOpen(false); }}><i className="fa fa-image" /> {text.imageDetect}</button>
          <button className={page === "video" ? "active" : ""} onClick={() => go("video")}><i className="fa fa-film" /> {text.videoDetect}</button>
          <button onClick={() => { window.location.href = REALGUARD_V2_CONSOLE_URL; }}><i className="fa fa-bolt" /> {text.v2}</button>
          <button className={page === "history" ? "active" : ""} onClick={() => go("history")}><i className="fa fa-clock-o" /> {text.history}</button>
          <button onClick={authAction}><i className={`fa ${user ? "fa-sign-out" : "fa-user"}`} /> {user ? text.logoutFull : text.loginRegister}</button>
        </div>
      </header>
      <nav className="mobile-bottom-nav">
        <button className={page === "home" ? "active" : ""} onClick={() => go("home")}><i className="fa fa-home" /><span>{text.mobileShort.home}</span></button>
        <button className={page === "image" ? "active" : ""} onClick={() => openImage("standard")}><i className="fa fa-image" /><span>{text.mobileShort.image}</span></button>
        <button className={page === "video" ? "active" : ""} onClick={() => go("video")}><i className="fa fa-film" /><span>{text.mobileShort.video}</span></button>
        <button className={page === "history" ? "active" : ""} onClick={() => go("history")}><i className="fa fa-clock-o" /><span>{text.mobileShort.history}</span></button>
      </nav>
    </>
  );
}

function HomePage({
  counters,
  setPage,
  openImage,
  lang
}: {
  counters: Counters;
  setPage: (page: PageKey) => void;
  openImage: (mode?: ImageDetectMode) => void;
  lang: Lang;
}) {
  const text = UI_TEXT[lang];
  const totalDetect = counters.image_detect + counters.video_detect || 10000;
  const workflowCards = [
    {
      step: "01",
      title: text.workflow[0][0],
      desc: text.workflow[0][1],
      action: text.workflow[0][2],
      icon: ShieldCheck,
      tone: "blue" as IconTone,
      onClick: () => openImage("standard"),
    },
    {
      step: "02",
      title: text.workflow[1][0],
      desc: text.workflow[1][1],
      action: text.workflow[1][2],
      icon: Network,
      tone: "red" as IconTone,
      onClick: () => openImage("swarm"),
    },
    {
      step: "03",
      title: text.workflow[2][0],
      desc: text.workflow[2][1],
      action: text.workflow[2][2],
      icon: FileArchive,
      tone: "green" as IconTone,
      onClick: () => setPage("history"),
    },
    {
      step: "04",
      title: text.workflow[3][0],
      desc: text.workflow[3][1],
      action: text.workflow[3][2],
      icon: Zap,
      tone: "amber" as IconTone,
      onClick: () => { window.location.href = REALGUARD_V2_CONSOLE_URL; },
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
            <div className="home-task-panel" aria-label={text.home.taskKicker}>
              <div className="home-task-panel-head">
                <span>{text.home.taskKicker}</span>
                <strong>{text.home.taskTitle}</strong>
              </div>
              <div className="home-task-grid">
                <button className="home-task-card primary" onClick={() => openImage("standard")}>
                  <ForensicIcon icon={ImageIcon} tone="blue" className="home-task-icon" />
                  <span>{text.home.primaryAction}</span>
                  <small>{lang === "zh" ? "开始普通图片检测" : "Start an image task"}</small>
                </button>
                <button className="home-task-card" onClick={() => setPage("video")}>
                  <ForensicIcon icon={Film} tone="ink" className="home-task-icon" />
                  <span>{text.home.videoAction}</span>
                  <small>{lang === "zh" ? "开始视频真伪检测" : "Start a video task"}</small>
                </button>
                <button className="home-task-card" onClick={() => { window.location.href = REALGUARD_V2_CONSOLE_URL; }}>
                  <ForensicIcon icon={Layers3} tone="amber" className="home-task-icon" />
                  <span>{text.home.secondaryAction}</span>
                  <small>{lang === "zh" ? "补充深度证据" : "Add deeper evidence"}</small>
                </button>
              </div>
              <div className="home-continue-row">
                <span>{text.home.continueKicker}</span>
                <button onClick={() => setPage("history")}><HistoryIcon size={15} strokeWidth={2} aria-hidden="true" />{text.home.historyAction}</button>
                <button onClick={() => setPage("history")}><FileText size={15} strokeWidth={2} aria-hidden="true" />{text.home.reportsAction}</button>
              </div>
            </div>
            <div className="home-trust-row" aria-label={lang === "zh" ? "平台能力摘要" : "Platform capability summary"}>
              <div>
                <strong>{lang === "zh" ? "任务分流" : "Task routing"}</strong>
                <span>{text.home.trust1}</span>
              </div>
              <div>
                <strong>{totalDetect.toLocaleString(localeFor(lang))}+</strong>
                <span>{text.home.trust2}</span>
              </div>
              <div>
                <strong>{lang === "zh" ? "报告" : "Reports"}</strong>
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
              <code>{lang === "zh" ? "检测记录 · 可复核报告" : "Detection record · reviewable report"}</code>
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
                <ForensicIcon icon={item.icon} tone={item.tone} className="workflow-icon" />
                <h3>{item.title}</h3>
                <p>{item.desc}</p>
                <strong>{item.action} <ArrowRight size={15} strokeWidth={2} aria-hidden="true" /></strong>
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="section section-alt home-capability-section">
        <div className="container">
          <SectionHeader title={text.home.capabilitiesTitle} desc={text.home.capabilitiesDesc} />
          <div className="features-grid">
            <FeatureCard accent="var(--primary)" tone="blue" icon={ImageIcon} title={text.features[0][0]} desc={text.features[0][1]} action={lang === "zh" ? "进入功能" : "Open tool"} onClick={() => openImage("standard")} />
            <FeatureCard accent="var(--warning)" tone="ink" icon={Film} title={text.features[1][0]} desc={text.features[1][1]} action={lang === "zh" ? "进入功能" : "Open tool"} onClick={() => setPage("video")} />
            <FeatureCard accent="var(--primary-dark)" tone="amber" icon={Layers3} title={text.features[2][0]} desc={text.features[2][1]} action={lang === "zh" ? "进入分析" : "Open analysis"} onClick={() => { window.location.href = REALGUARD_V2_CONSOLE_URL; }} />
            <FeatureCard accent="var(--danger)" tone="red" icon={Network} title={text.features[3][0]} desc={text.features[3][1]} action={lang === "zh" ? "进入会诊" : "Open expert review"} onClick={() => openImage("swarm")} />
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

function ForensicIcon({ icon: Icon, tone = "blue", className = "" }: { icon: LucideIcon; tone?: IconTone; className?: string }) {
  return (
    <span className={`forensic-icon forensic-icon-${tone}${className ? ` ${className}` : ""}`} aria-hidden="true">
      <Icon size={20} strokeWidth={1.9} />
    </span>
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

function FeatureCard({ accent, icon, tone = "blue", title, desc, action, onClick }: { accent: string; icon: LucideIcon; tone?: IconTone; title: string; desc: string; action: string; onClick: () => void }) {
  return (
    <button className="feature-card fade-up visible" style={{ "--card-accent": accent } as React.CSSProperties} onClick={onClick}>
      <ForensicIcon icon={icon} tone={tone} className="feature-icon" />
      <h3>{title}</h3>
      <p>{desc}</p>
      <span className="feature-link">
        {action} <ArrowRight size={15} strokeWidth={2} aria-hidden="true" />
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
  void lang;
  return null;
}

function AccessLoginGate({ lang, onLogin }: { lang: Lang; onLogin: () => void }) {
  const isZh = lang === "zh";
  return (
    <main className="main">
      <section className="page-hero">
        <div className="container">
          <div className="section-label"><i className="fa fa-lock" /> {isZh ? "账号登录" : "Account Login"}</div>
          <h1>{isZh ? "请先登录后继续" : "Please log in to continue"}</h1>
          <p>{isZh ? "账号登录后可查看检测记录、报告归档、复核状态和管理入口。" : "After account login, you can review detection records, report archives, review status, and management entries."}</p>
          <button className="btn-primary" onClick={onLogin}>
            <i className="fa fa-user" /> {isZh ? "登录/注册" : "Log in / Sign up"}
          </button>
        </div>
      </section>
    </main>
  );
}

function AccessPlatformPage({ lang, onNeedAuth }: { lang: Lang; onNeedAuth: () => void }) {
  return <AccessLoginGate lang={lang} onLogin={onNeedAuth} />;
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
  initialMode,
  isGuest,
  guestDetections,
  onNeedAuth,
  onDone
}: {
  lang: Lang;
  initialMode: ImageDetectMode;
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
  const [detectMode, setDetectMode] = useState<ImageDetectMode>(initialMode);
  const [swarmJob, setExpertReviewJob] = useState<DetectionJob | null>(null);
  const swarmRunTokenRef = useRef(0);
  const swarmAbortRef = useRef<AbortController | null>(null);

  function cancelExpertReviewRun() {
    swarmRunTokenRef.current += 1;
    swarmAbortRef.current?.abort();
    swarmAbortRef.current = null;
  }

  useEffect(() => {
    cancelExpertReviewRun();
    setDetectMode(initialMode);
    setResult(null);
    setExpertReviewJob(null);
  }, [initialMode]);

  useEffect(() => () => {
    cancelExpertReviewRun();
  }, []);

  useEffect(() => () => {
    if (preview.startsWith("blob:")) URL.revokeObjectURL(preview);
  }, [preview]);

  function selectFile(next: File | null) {
    cancelExpertReviewRun();
    if (next) {
      const message = validateFile(next, { kind: imageKind, maxBytes: IMAGE_MAX_BYTES, mimePrefixes: ["image/"], lang });
      if (message) {
        setStatus({ tone: "error", text: message });
        return;
      }
    }
    setFile(next);
    setResult(null);
    setExpertReviewJob(null);
    setPreview(next ? URL.createObjectURL(next) : "");
    setStatus({ tone: "info", text: next ? tr(`已选择: ${next.name}`, `Selected: ${next.name}`) : tr("等待上传图片...", "Waiting for image upload...") });
  }

  async function runExpertReview(nextFile: File) {
    cancelExpertReviewRun();
    const controller = new AbortController();
    swarmAbortRef.current = controller;
    const runToken = swarmRunTokenRef.current;
    const assertActive = () => {
      if (controller.signal.aborted || swarmRunTokenRef.current !== runToken) throw new Error(SWARM_CANCELLED_ERROR);
    };
    setExpertReviewJob(null);
    setStatus({ tone: "info", text: tr("专家会诊复核启动中……", "Starting expert review...") });
    const started = await startExpertReviewImageDetection(nextFile, controller.signal);
    assertActive();
    let current = started.job;
    setExpertReviewJob(current);
    const startedAt = Date.now();
    while (Date.now() - startedAt < 120000) {
      if (current.status === "success") {
        const nextResult = current.result?.result;
        if (!nextResult) throw new Error(tr("专家会诊复核已完成，但没有返回检测结果", "Expert review finished without a detection result"));
        assertActive();
        setResult(nextResult);
        setStatus({ tone: "ok", text: tr("专家会诊复核完成", "Expert review complete") });
        await onDone();
        assertActive();
        swarmAbortRef.current = null;
        return;
      }
      if (current.status === "failed") {
        swarmAbortRef.current = null;
        throw new Error(tr("专家会诊复核暂不可用，请稍后重试", "Expert review is temporarily unavailable. Please try again later."));
      }
      await new Promise((resolve) => window.setTimeout(resolve, 760));
      assertActive();
      const polled = await getImageDetectionJob(current.id, controller.signal);
      assertActive();
      current = polled.job;
      setExpertReviewJob(current);
      setStatus({ tone: "info", text: publicExpertReviewJobSummary(current, lang) });
    }
    swarmAbortRef.current = null;
    throw new Error(tr("专家会诊复核超时，请稍后在历史记录查看结果", "Expert review timed out. Check history later."));
  }

  async function runDetection(nextFile: File) {
    setResult(null);
    if (detectMode === "swarm") {
      await runExpertReview(nextFile);
      return;
    }
    cancelExpertReviewRun();
    const controller = new AbortController();
    swarmAbortRef.current = controller;
    const runToken = swarmRunTokenRef.current;
    const assertActive = () => {
      if (controller.signal.aborted || swarmRunTokenRef.current !== runToken) throw new Error(SWARM_CANCELLED_ERROR);
    };
    setExpertReviewJob(null);
    setStatus({ tone: "info", text: tr("正在分析图像……", "Analyzing image...") });
    const data = await detectImage(nextFile, controller.signal);
    assertActive();
    setResult(data.result);
    setStatus({ tone: "ok", text: tr("检测完成", "Detection complete") });
    await onDone();
    assertActive();
    swarmAbortRef.current = null;
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
    const requestedMode = detectMode;
    try {
      await runDetection(file);
    } catch (error) {
      if (!isExpertReviewCancelledError(error)) {
        if (requestedMode === "swarm") console.warn("ExpertReview detection failed", error);
        setStatus({ tone: "error", text: publicDetectionErrorMessage(error, requestedMode, lang) });
      }
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
    const requestedMode = detectMode;
    cancelExpertReviewRun();
    const sampleController = new AbortController();
    swarmAbortRef.current = sampleController;
    const sampleToken = swarmRunTokenRef.current;
    const assertSampleActive = () => {
      if (sampleController.signal.aborted || swarmRunTokenRef.current !== sampleToken) throw new Error(SWARM_CANCELLED_ERROR);
    };
    setStatus({ tone: "info", text: tr(`正在加载示例图片：${sample.title}`, `Loading sample image: ${sample.title}`) });
    try {
      const response = await fetch(sample.image, { signal: sampleController.signal });
      assertSampleActive();
      if (!response.ok) {
        throw new Error(tr(`示例图片加载失败：${response.status}`, `Sample image failed to load: ${response.status}`));
      }
      const blob = await response.blob();
      assertSampleActive();
      const ext = sample.image.split(".").pop()?.split("?")[0] || "jpg";
      const sampleFile = new File([blob], `${sample.title}.${ext}`, {
        type: blob.type || "image/jpeg"
      });
      setFile(sampleFile);
      setPreview(URL.createObjectURL(sampleFile));
      await runDetection(sampleFile);
    } catch (error) {
      if (!isExpertReviewCancelledError(error)) {
        if (requestedMode === "swarm") console.warn("ExpertReview sample detection failed", error);
        setStatus({ tone: "error", text: publicDetectionErrorMessage(error, requestedMode, lang) });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="main">
      <div className="container">
        <PageHeader icon="fa-image" title={pageText.imageTitle} desc={pageText.imageDesc} />
        <div className={`layout ${detectMode === "swarm" ? "swarm-layout" : ""}`}>
          <div className={`card ${detectMode === "swarm" ? "swarm-control-card" : ""}`}>
            <div className="section-label"><i className="fa fa-cogs" /> {tr("选择鉴伪任务", "Select forensic task")}</div>
            <div className="model-tabs" aria-label={tr("鉴伪任务模式", "Forensic task mode")}>
              <button className={`model-tab ${detectMode === "standard" ? "active" : ""}`} type="button" aria-pressed={detectMode === "standard"} disabled={busy} onClick={() => setDetectMode("standard")}><i className="fa fa-magic" aria-hidden="true" /> {tr("标准检测", "Standard")}</button>
              <button className={`model-tab ${detectMode === "swarm" ? "active" : ""}`} type="button" aria-pressed={detectMode === "swarm"} disabled={busy} onClick={() => setDetectMode("swarm")}><i className="fa fa-sitemap" aria-hidden="true" /> {tr("专家会诊", "Expert review")}</button>
            </div>
            <div className="model-desc">
              <strong>{detectMode === "swarm" ? tr("专家会诊复核：", "Expert review: ") : tr("标准检测：", "Standard detection: ")}</strong>
              {detectMode === "swarm"
                ? tr("调度多类鉴伪专家进行投票复核，只展示综合意见、共识度和关键分歧。", "Runs multiple forensic experts for a voted review and shows the combined opinion, consensus, and key disagreements.")
                : tr("分析图像是否存在生成式内容风险，并结合元数据做辅助展示。", "Analyzes generated-content risk and uses metadata as supporting context.")}
            </div>
            <div className="card-divider" />
            <div className="section-label"><i className="fa fa-upload" /> {tr("上传图片", "Upload image")}</div>
            {isGuest && <TrialHint used={guestDetections} lang={lang} />}
            <UploadBox accept="image/*" file={file} preview={preview} onFile={selectFile} kind={imageKind} lang={lang} />
            <button className={`btn-primary ${busy ? "detecting" : ""}`} disabled={!file || busy} onClick={submit}>
              <i className={`fa ${busy ? "fa-circle-o-notch detect-spin" : "fa-search"}`} /> {busy ? tr("正在分析", "Analyzing") : tr("开始检测", "Start detection")}
            </button>
          </div>
          {detectMode === "swarm" ? (
            <>
              <div className="card swarm-status-card">
                <div className="section-label"><i className="fa fa-info-circle" /> {tr("当前状态", "Current status")}</div>
                <StatusRow status={status} busy={busy} />
                <div className="card-divider" />
                {result ? <ImageResult result={result} lang={lang} /> : <ImageSamples onSelect={detectSample} busy={busy} lang={lang} />}
              </div>
              <div className="card swarm-stage-card">
                <ExpertReviewJobPanel job={swarmJob} busy={busy} lang={lang} />
              </div>
            </>
          ) : (
            <div className="card">
              <div className="section-label"><i className="fa fa-info-circle" /> {tr("当前状态", "Current status")}</div>
              <StatusRow status={status} busy={busy} />
              <div className="card-divider" />
              {result ? <ImageResult result={result} lang={lang} /> : <ImageSamples onSelect={detectSample} busy={busy} lang={lang} />}
            </div>
          )}
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
      targetTab === "video"
        ? getHistory("video-detections", { query: debouncedQuery, filter: activeFilter, limit, offset })
        : getHistory("image-detections", { query: debouncedQuery, filter: activeFilter, limit, offset });
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
    return [
      { label: tr("当前记录", "Current records"), value: historyFilterCounts.all ?? historyTotal, filterKey: "all" as HistoryFilterKey },
      { label: tr("访客记录", "Guest records"), value: historyFilterCounts.guest ?? 0, filterKey: "guest" as HistoryFilterKey },
      { label: tr("生成结论", "AI verdicts"), value: historyFilterCounts.ai ?? 0, filterKey: "ai" as HistoryFilterKey },
      { label: tr("真实结论", "Real verdicts"), value: historyFilterCounts.real ?? 0, filterKey: "real" as HistoryFilterKey },
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
                    placeholder={tr("按文件名、结论、时间搜索历史记录", "Search by filename, verdict, or time")}
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
                    { label: tab === "video" ? UI_TEXT[lang].pages.videoTitle : UI_TEXT[lang].pages.imageTitle, onClick: () => setPage(tab === "video" ? "video" : "image") },
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
                { label: tab === "video" ? UI_TEXT[lang].pages.videoTitle : UI_TEXT[lang].pages.imageTitle, onClick: () => setPage(tab === "video" ? "video" : "image") },
              ]}
            />
          ) : !status && (filter !== "all" || query.trim()) ? (
            <EmptyState
              icon="fa-filter"
              text={tr("当前筛选条件下暂无记录", "No records match the current filters")}
              actions={[
                { label: tr("清除条件", "Clear filters"), onClick: () => { updateHistoryFilter("all"); updateHistoryQuery(""); } },
                { label: tab === "video" ? UI_TEXT[lang].pages.videoTitle : UI_TEXT[lang].pages.imageTitle, onClick: () => setPage(tab === "video" ? "video" : "image") },
              ]}
            />
          ) : !status && (
            <EmptyState
              icon="fa-clock-o"
              text={tr("暂无记录", "No records yet")}
              actions={[
                { label: UI_TEXT[lang].pages.imageTitle, onClick: () => setPage("image") },
                { label: UI_TEXT[lang].pages.videoTitle, onClick: () => setPage("video") },
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
  const [dragging, setDragging] = useState(false);
  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) onFile(dropped);
  }
  return (
    <div
      className={`upload-area ${dragging ? "drag-over" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
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
          <button className="clear-btn" type="button" onClick={() => onFile(null)}><i className="fa fa-times" /> {tr("清除", "Clear")}</button>
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
        tr("生成内容鉴伪：识别疑似生成、合成或编辑的内容风险", "Generated-content forensics: identifies suspected generated, synthetic, or edited content risk"),
        tr("篡改痕迹鉴伪：识别拼接、修补、克隆等后处理痕迹", "Tamper-trace forensics: identifies splicing, inpainting, cloning, and related traces"),
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

function ExpertReviewJobPanel({ job, busy, lang }: { job: DetectionJob | null; busy: boolean; lang: Lang }) {
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  const progress = Math.max(0, Math.min(100, Math.round(Number(job?.progress || 0))));
  const experts = job?.experts || [];
  const activeExperts = experts.filter((expert) => expert.status === "running").length;
  const finishedExperts = experts.filter((expert) => ["success", "failed", "skipped"].includes(String(expert.status || ""))).length;
  const visualExperts = experts.length ? experts : SWARM_PLACEHOLDER_EXPERTS;
  const summary = publicExpertReviewJobSummary(job, lang);
  const consensusScore = job?.result?.result?.swarm?.consensusScore;
  const hasConsensus = typeof consensusScore === "number" && Number.isFinite(consensusScore);
  const consensusPercent = hasConsensus ? Math.round(Math.max(0, Math.min(1, Number(consensusScore))) * 100) : progress;
  const consensusLabel = hasConsensus ? tr("共识度", "Consensus") : tr("检测进度", "Progress");
  const visualState = busy || job?.status === "running" ? "active" : "calm";
  const liveProgress = job?.status === "success" || job?.status === "failed" ? progress : Math.floor(progress / 25) * 25;
  const liveText = `${summary}，${tr("进度", "progress")} ${liveProgress}%`;
  return (
    <div
      className="swarm-job-panel"
      aria-busy={busy}
      aria-label={`${summary}，${tr("进度", "progress")} ${progress}%`}
    >
      <span className="sr-only" aria-live="polite">{liveText}</span>
      <div className="swarm-job-head">
        <span><i className="fa fa-sitemap" aria-hidden="true" /> {tr("专家会诊复核", "Expert review")}</span>
        <strong>{progress}%</strong>
      </div>
      <div className="swarm-command-grid">
        <div className={`swarm-stage-viz ${job?.status || "queued"} ${visualState}`}>
          <div className="swarm-rain" aria-hidden="true">
            {SWARM_RAIN_DROPS.map((index) => <span key={index} style={{ "--rain-index": index } as React.CSSProperties} />)}
          </div>
          <div className="swarm-orbit-field" aria-hidden="true">
            <div className="swarm-orbit orbit-a" />
            <div className="swarm-orbit orbit-b" />
            <div className="swarm-orbit orbit-c" />
            <div className="swarm-constellation-lines" />
            <div className="swarm-core-viz">
              <span className="swarm-core-pulse" />
              <span className="swarm-core-ring" />
              <i className="fa fa-fingerprint" />
              <b>{tr("会诊中", "Reviewing")}</b>
            </div>
            <div className="swarm-scanline" />
            {visualExperts.slice(0, 8).map((expert, index) => (
              <span
                className={`swarm-node ${normalizeExpertReviewStatus(expert.status)}`}
                style={{ "--node-index": index } as React.CSSProperties}
                key={`${expert.publicId || expert.id || index}-viz`}
              >
                <i>{String(index + 1).padStart(2, "0")}</i>
              </span>
            ))}
          </div>
        </div>
        <aside className="swarm-briefing-side">
          <div className="swarm-job-summary">
            <strong>{summary}</strong>
            {!job && <span>{tr("上传图片并点击开始检测后，系统会组织多名鉴伪专家进行并行复核。", "Upload an image and start detection to run a parallel expert review.")}</span>}
            {job && <span>{tr("页面展示综合进度与共识信号，详细过程用于质量追踪。", "This page shows aggregate progress and consensus; detailed process data supports quality tracking.")}</span>}
          </div>
          <div className="swarm-viz-stats">
            <span>{tr("活跃专家", "Active experts")} <b>{activeExperts}</b></span>
            <span>{tr("已完成", "Completed")} <b>{finishedExperts}</b></span>
            <span>{consensusLabel} <b>{consensusPercent}%</b></span>
          </div>
          <div
            className="swarm-progress-track"
            role="progressbar"
            aria-label={tr("专家会诊进度", "Expert review progress")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <span style={{ width: `${progress}%` }} />
          </div>
          <div className="swarm-expert-grid" aria-label={tr("匿名专家队列", "Anonymous expert queue")}>
            {visualExperts.slice(0, 8).map((expert, index) => {
              const status = normalizeExpertReviewStatus(expert.status);
              return (
                <div className={`swarm-expert-card ${status}`} key={`${expert.publicId || expert.id || index}-card`}>
                  <span className="swarm-expert-icon"><i className="fa fa-user-secret" aria-hidden="true" /></span>
                  <span className="swarm-expert-body">
                    <strong>{publicExpertReviewExpertName(expert, index, lang)}</strong>
                    <em>{publicExpertReviewExpertMessage(expert, lang)}</em>
                  </span>
                  <b>{publicExpertReviewExpertStatusLabel(expert.status, lang)}</b>
                </div>
              );
            })}
          </div>
        </aside>
      </div>
    </div>
  );
}

function ImageResult({ result, lang }: { result: ImageDetectionResult; lang: Lang }) {
  const probability = Math.round((result.probability || 0) * 1000) / 10;
  const swarm = result.swarm;
  const swarmExperts = swarm?.experts || [];
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
      {swarm?.enabled && (
        <div className="swarm-result-panel">
          <div className="swarm-result-head">
            <h4><i className="fa fa-sitemap" /> {tr("专家会诊意见", "Expert review opinion")}</h4>
            <div className="swarm-result-badges">
              <span>{tr("有效专家", "Effective experts")} {swarm.effectiveExperts || 0}/{swarm.totalExperts || swarmExperts.length}</span>
              <span>{tr("共识", "Consensus")} {Math.round(Number(swarm.consensusScore || 0) * 100)}%</span>
              {swarm.disagreement && <span className="warning">{tr("存在分歧", "Disagreement")}</span>}
            </div>
          </div>
          {swarm.evidence && swarm.evidence.length > 0 && (
            <ul className="swarm-evidence-list">
              {swarm.evidence.slice(0, 4).map((item, index) => <li key={`${index}-${item}`}>{publicExpertReviewEvidence(item, lang)}</li>)}
            </ul>
          )}
        </div>
      )}
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

function HistoryRecords({
  records,
  tab,
  query,
  lang,
}: {
  records: HistoryRecord[];
  tab: HistoryTabKey;
  query: string;
  lang: Lang;
}) {
  const isVideo = tab === "video";
  const tr = (zh: string, en: string) => translate(lang, zh, en);
  return (
    <div className="history-grid">
      {records.map((record, index) => {
        const mediaUrl = historyMediaUrl(record);
        const previewUrl = historyPreviewUrl(record) || mediaUrl;
        const title = String(record.filename || tr(`历史记录 ${index + 1}`, `History record ${index + 1}`));
        const verdict = String(record.final_label || "-");
        const meta = String(record.confidence || "-");
        const reportUrl = String(record.report_url || "");
        const guestRecord = Boolean(record.is_guest_record);
        const hasMetadata = Boolean(record.has_metadata);
        const hasIssues = Boolean(record.has_visual_issues);
        const issueCount = Number(record.visual_issue_count || 0);
        const timeText = String(record.createtime || "-");
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
              <div className="history-row"><span>{tr("时间", "Time")}</span><strong>{renderHighlightedText(timeText, query)}</strong></div>
              <div className="history-row"><span>{tr("结论", "Verdict")}</span><strong>{renderHighlightedText(verdict, query)}</strong></div>
              <div className="history-row"><span>{tr("置信度", "Confidence")}</span><strong>{renderHighlightedText(meta, query)}</strong></div>
              {reportUrl && (
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
      <div className="login-card modal-login-card auth-shell">
        <button className="case-modal-close modal-close" onClick={onClose}><i className="fa fa-times" /></button>
        <aside className="auth-rail" aria-label={text.railTitle}>
          <div className="auth-rail-mark"><i className="fa fa-shield" /></div>
          <h3>{text.railTitle}</h3>
          <p>{text.railDesc}</p>
          <div className="auth-rail-points">
            {text.railPoints.map((item) => <span key={item}><i className="fa fa-check" /> {item}</span>)}
          </div>
        </aside>
        <div className="auth-main">
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
    </div>
  );
}

function AuthForm({ onAuthed, lang }: { onAuthed: () => Promise<void>; lang: Lang }) {
  const [mode, setMode] = useState<AuthMode>("password");
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [username, setUsername] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
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

  function switchMode(nextMode: AuthMode) {
    setMode(nextMode);
    setStatus(null);
    setSmsCode("");
    setSecret("");
    setCooldown(0);
  }

  function passwordPolicyMessage(value: string) {
    if (value.length < 8) return tr("密码至少需要 8 位", "Password must be at least 8 characters");
    if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) return tr("密码需同时包含字母和数字", "Password must include letters and numbers");
    return "";
  }

  async function sendCode(scene: "login" | "register" | "reset") {
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
        setStatus({ tone: "ok", text: tr(`测试验证码已自动填入：${data.debug_code}`, `Test code filled automatically: ${data.debug_code}`) });
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
    if ((mode === "sms" || mode === "register" || mode === "reset") && !smsCode.trim()) {
      setStatus({ tone: "error", text: tr("请输入短信验证码", "Enter the SMS code") });
      return;
    }
    if ((mode === "password" || mode === "sms" || mode === "register") && !acceptedTerms) {
      setStatus({ tone: "error", text: text.termsRequired });
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      if (mode === "password") await loginByPassword(phone, secret, acceptedTerms);
      else if (mode === "sms") await loginBySms(phone, smsCode, acceptedTerms);
      else if (mode === "register") {
        const passwordError = passwordPolicyMessage(secret);
        if (passwordError) {
          setStatus({ tone: "error", text: passwordError });
          return;
        }
        await registerUser({ phone, secret, username, sms_code: smsCode, accepted_terms: acceptedTerms, terms_version: REALGUARD_TERMS_VERSION });
        setStatus({ tone: "ok", text: tr("注册成功，请切换到登录", "Account created. Switch to log in.") });
        setMode("password");
        setSmsCode("");
        setSecret("");
        setAcceptedTerms(false);
        return;
      } else {
        const passwordError = passwordPolicyMessage(secret);
        if (passwordError) {
          setStatus({ tone: "error", text: passwordError });
          return;
        }
        await resetPassword({ phone, secret, sms_code: smsCode });
        setStatus({ tone: "ok", text: tr("密码已重置，请使用新密码登录", "Password reset. Log in with the new password.") });
        setMode("password");
        setSmsCode("");
        setSecret("");
        return;
      }
      await onAuthed();
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  const codeScene: "login" | "register" | "reset" = mode === "register" ? "register" : mode === "reset" ? "reset" : "login";
  const needsSms = mode === "sms" || mode === "register" || mode === "reset";
  const needsPassword = mode === "password" || mode === "register" || mode === "reset";
  const passwordLabel = mode === "reset" ? text.newPasswordLabel : text.passwordLabel;
  const passwordPlaceholder = mode === "password" ? text.passwordPlaceholder : text.newPasswordPlaceholder;
  const submitText = mode === "register" ? text.create : mode === "reset" ? text.resetAction : text.login;
  const submitIcon = mode === "register" ? "fa-user-plus" : mode === "reset" ? "fa-refresh" : "fa-sign-in";
  const requiresTerms = mode !== "reset";

  return (
    <>
      <div className="login-tabs">
        <button type="button" className={`login-tab ${mode === "password" ? "active" : ""}`} onClick={() => switchMode("password")}>{text.password}</button>
        <button type="button" className={`login-tab ${mode === "sms" ? "active" : ""}`} onClick={() => switchMode("sms")}>{text.sms}</button>
        <button type="button" className={`login-tab ${mode === "register" ? "active" : ""}`} onClick={() => switchMode("register")}>{text.register}</button>
        <button type="button" className={`login-tab ${mode === "reset" ? "active" : ""}`} onClick={() => switchMode("reset")}>{text.reset}</button>
      </div>
      <form onSubmit={submit} className="login-panel active">
        <AuthInput icon="fa-phone" label={text.phone} value={phone} onChange={setPhone} placeholder={text.phonePlaceholder} inputMode="numeric" autoComplete="tel" maxLength={11} />
        {mode === "register" && <AuthInput icon="fa-user" label={text.username} value={username} onChange={setUsername} placeholder={text.usernamePlaceholder} autoComplete="name" maxLength={64} />}
        {needsPassword && (
          <>
            <AuthInput
              icon="fa-lock"
              label={passwordLabel}
              value={secret}
              onChange={setSecret}
              placeholder={passwordPlaceholder}
              type={showSecret ? "text" : "password"}
              autoComplete={mode === "password" ? "current-password" : "new-password"}
              rightAction={(
                <button type="button" className="password-toggle" onClick={() => setShowSecret((value) => !value)} aria-label={showSecret ? tr("隐藏密码", "Hide password") : tr("显示密码", "Show password")}>
                  <i className={`fa ${showSecret ? "fa-eye-slash" : "fa-eye"}`} />
                </button>
              )}
            />
            {(mode === "register" || mode === "reset") && <p className="password-hint">{text.passwordHint}</p>}
          </>
        )}
        {needsSms && (
          <div className="form-group">
            <label className="form-label">{text.smsCode}</label>
            <div className="code-row">
              <div className="input-wrap">
                <i className="fa fa-shield" />
                <input value={smsCode} onChange={(event) => setSmsCode(event.target.value)} placeholder={text.smsPlaceholder} inputMode="numeric" autoComplete="one-time-code" maxLength={8} />
              </div>
              <button
                type="button"
                className="btn-code"
                disabled={codeBusy || cooldown > 0}
                onClick={() => sendCode(codeScene)}
              >
                {codeBusy ? text.sending : cooldown > 0 ? `${cooldown}s` : text.sendCode}
              </button>
            </div>
          </div>
        )}
        {requiresTerms && (
          <label className="terms-check">
            <input type="checkbox" checked={acceptedTerms} onChange={(event) => setAcceptedTerms(event.target.checked)} />
            <span>
              {text.termsPrefix} <a href="/legal/terms.html" target="_blank" rel="noreferrer">{text.terms}</a> {text.termsJoin} <a href="/legal/privacy.html" target="_blank" rel="noreferrer">{text.privacy}</a>
            </span>
          </label>
        )}
        {status && <div className={`notice ${status.tone}`}>{status.text}</div>}
        <button type="submit" className="btn-primary" disabled={busy}><i className={`fa ${submitIcon}`} /> {submitText}</button>
        <div className="auth-foot-actions">
          {mode !== "reset" && <button type="button" onClick={() => switchMode("reset")}>{text.forgot}</button>}
          {mode === "reset" && <button type="button" onClick={() => switchMode("password")}>{text.backLogin}</button>}
        </div>
      </form>
    </>
  );
}

function AuthInput({
  icon,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  autoComplete,
  inputMode,
  maxLength,
  rightAction,
}: {
  icon: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  type?: string;
  autoComplete?: string;
  inputMode?: InputHTMLAttributes<HTMLInputElement>["inputMode"];
  maxLength?: number;
  rightAction?: ReactNode;
}) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <div className={`input-wrap ${rightAction ? "has-action" : ""}`}>
        <i className={`fa ${icon}`} />
        <input type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoComplete={autoComplete} inputMode={inputMode} maxLength={maxLength} />
        {rightAction && <span className="input-action">{rightAction}</span>}
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
      <p className="footer-icp">
        <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">
          {text.icp}
        </a>
      </p>
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

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作失败";
}

function isExpertReviewCancelledError(error: unknown) {
  return errorMessage(error) === SWARM_CANCELLED_ERROR || (error instanceof DOMException && error.name === "AbortError");
}

function publicDetectionErrorMessage(error: unknown, mode: ImageDetectMode, lang: Lang) {
  if (mode === "swarm") {
    return translate(lang, "专家会诊复核暂不可用，请稍后重试", "Expert review is temporarily unavailable. Please try again later.");
  }
  return errorMessage(error);
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
  return value && ["home", "image", "video", "history", "developer"].includes(value) ? value : "home";
}

function getInitialImageMode(): ImageDetectMode {
  if (typeof window === "undefined") return "standard";
  const value = new URLSearchParams(window.location.search).get("imageMode");
  return value === "swarm" ? "swarm" : "standard";
}

function getInitialHistoryTab(): HistoryTabKey {
  if (typeof window === "undefined") return "image";
  const value = new URLSearchParams(window.location.search).get("historyTab") as HistoryTabKey | null;
  return value && ["image", "video"].includes(value) ? value : "image";
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
  return [
    String(record.filename || ""),
    String(record.final_label || ""),
    String(record.confidence || ""),
    String(record.createtime || ""),
    Boolean(record.is_guest_record) ? "访客" : "",
    Boolean(record.has_metadata) ? "元数据" : "",
    Boolean(record.has_visual_issues) ? `可疑点${issueCount > 0 ? ` ${issueCount}` : ""}` : "",
    "结论",
    "置信度",
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
  return [
    { key: "all", label: translate(lang, "全部", "All") },
    { key: "guest", label: translate(lang, "访客", "Guest") },
    { key: "ai", label: translate(lang, "生成结论", "AI verdicts") },
    { key: "real", label: translate(lang, "真实结论", "Real verdicts") },
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
  if (filter === "guest") return Boolean(record.is_guest_record);
  if (filter === "ai") return String(record.final_label || "").includes("AI");
  if (filter === "real") return String(record.final_label || "").includes("真实");
  return true;
}

function isHistoryFilterSupported(tab: HistoryTabKey, filter: HistoryFilterKey) {
  return getHistoryFilterOptions(tab).some((option) => option.key === filter) || filter === "all";
}

function getHistoryActiveSummary(tab: HistoryTabKey, filter: HistoryFilterKey, query: string, lang: Lang = "zh") {
  const tabLabels: Record<HistoryTabKey, string> = {
    image: translate(lang, "图像鉴伪", "Image forensics"),
    video: translate(lang, "视频鉴伪", "Video forensics"),
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
