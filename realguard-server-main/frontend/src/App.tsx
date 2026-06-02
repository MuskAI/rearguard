import { FormEvent, Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  Counters,
  HistoryFilterKey,
  HistoryListResponse,
  HistoryRecord,
  ImageDetectionResult,
  RetrieveItem,
  User,
  VideoDetectionResult,
  detectImage,
  detectVideo,
  downloadImageReport,
  downloadRetrieveReport,
  downloadVideoReport,
  getHistory,
  getLibraries,
  getMe,
  getRetrievalHistory,
  loginByPassword,
  loginBySms,
  logout,
  registerUser,
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

const emptyCounters: Counters = {
  image_detect: 0,
  video_detect: 0,
  image_retrieve: 0,
  video_retrieve: 0
};
const HISTORY_PAGE_SIZE = 100;
const REALGUARD_API_BASE = "http://124.222.3.205/v2-api";
const REALGUARD_SKILL_URL = "http://124.222.3.205/skills/realguard-forensics/SKILL.md";
const REALGUARD_API_DOC_URL = "http://124.222.3.205/developer/API.md";
const REALGUARD_SKILL_HANDOFF =
  `Use $realguard-forensics; read ${REALGUARD_SKILL_URL}; call POST http://124.222.3.205/v2-api/detect with multipart field file, or run python3 scripts/realguard_cli.py detect <file> --base-url http://124.222.3.205 --api-prefix /v2-api --pretty if the repo CLI is available; then return a concise verdict with confidence, evidence, model version, cache version, and report id.`;
const REALGUARD_SKILL_COMMAND =
  "python3 scripts/realguard_cli.py detect <file> --base-url http://124.222.3.205 --api-prefix /v2-api --pretty";

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [counters, setCounters] = useState<Counters>(emptyCounters);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<PageKey>(() => getInitialPage());
  const [authOpen, setAuthOpen] = useState(false);
  const [guestDetections, setGuestDetections] = useState(() => getGuestDetections());
  const [dark, setDark] = useState(() => getStorage()?.getItem("theme") === "dark");
  const deviceType = useDeviceType();

  useEffect(() => {
    document.body.toggleAttribute("data-theme", dark);
    getStorage()?.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    document.body.dataset.device = deviceType;
  }, [deviceType]);

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
        <span>正在连接系统...</span>
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
        onLogin={requireAuth}
        onLogout={handleLogout}
      />

      {!user && (
        <div className="trial-strip">
          <div className="container">
            <span className="trial-icon"><i className="fa fa-info-circle" /></span>
            <span className="trial-copy">
              <strong>访客体验</strong>
              <span>首次检测无需登录，第二次检测前请登录。</span>
            </span>
            <button onClick={requireAuth}>登录/注册</button>
          </div>
        </div>
      )}

      {page === "home" && <HomePage counters={counters} setPage={setPage} />}
      {page === "image" && (
        <ImageDetectionPage
          isGuest={!user}
          guestDetections={guestDetections}
          onNeedAuth={requireAuth}
          onDone={handleDetectionDone}
        />
      )}
      {page === "video" && (
        <VideoDetectionPage
          isGuest={!user}
          guestDetections={guestDetections}
          onNeedAuth={requireAuth}
          onDone={handleDetectionDone}
        />
      )}
      {page === "retrieve" && <RetrievePage onDone={refreshMe} />}
      {page === "history" && <HistoryPage setPage={setPage} />}
      {page === "developer" && <DeveloperPlatformPage />}

      <Footer />

      {authOpen && (
        <AuthModal
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
  onLogin,
  onLogout
}: {
  page: PageKey;
  setPage: (page: PageKey) => void;
  user: User | null;
  dark: boolean;
  setDark: (value: boolean) => void;
  onLogin: () => void;
  onLogout: () => void;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
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
            <span className="logo-full">数字内容鉴伪平台</span>
            <span className="logo-mobile">RealGuard</span>
          </button>
          <nav className="nav-links">
            <button className={page === "home" ? "active" : ""} onClick={() => go("home")}>
              首页
            </button>
            <div className="dropdown">
              <button className={`dropdown-trigger ${["image", "video", "retrieve"].includes(page) ? "active" : ""}`}>
                <span>功能</span>
                <i className="fa fa-chevron-down" />
              </button>
              <div className="dropdown-menu">
                <div className="dropdown-label">检测</div>
                <button className={`dropdown-item ${page === "image" ? "active-item" : ""}`} onClick={() => go("image")}>
                  <i className="fa fa-image" /> 图像鉴伪
                </button>
                <button className={`dropdown-item ${page === "video" ? "active-item" : ""}`} onClick={() => go("video")}>
                  <i className="fa fa-film" /> 视频鉴伪
                </button>
                <div className="dropdown-divider" />
                <div className="dropdown-label">检索</div>
                <button className={`dropdown-item ${page === "retrieve" ? "active-item" : ""}`} onClick={() => go("retrieve")}>
                  <i className="fa fa-search" /> 图像侵权检索
                </button>
                <button className="dropdown-item" onClick={() => go("retrieve")}>
                  <i className="fa fa-play-circle" /> 视频侵权检索
                </button>
              </div>
            </div>
            <button className={page === "history" ? "active" : ""} onClick={() => go("history")}>
              历史记录
            </button>
            <button className={page === "developer" ? "active" : ""} onClick={() => go("developer")}>
              开发者平台
            </button>
            <button onClick={() => { window.location.href = "/v2/"; }}>V2 Agent</button>
            <button onClick={authAction}>{user ? "退出" : "登录"}</button>
            <button className="theme-btn" title="切换主题" onClick={() => setDark(!dark)}>
              <i className={`fa ${dark ? "fa-sun-o" : "fa-moon-o"}`} />
            </button>
          </nav>
          <div className="mobile-nav-actions">
            <button className="theme-btn" title="切换主题" onClick={() => setDark(!dark)}>
              <i className={`fa ${dark ? "fa-sun-o" : "fa-moon-o"}`} />
            </button>
            <button className="mobile-menu-btn" aria-label="打开菜单" onClick={() => setMobileOpen(!mobileOpen)}>
              <i className={`fa ${mobileOpen ? "fa-times" : "fa-bars"}`} />
              <span>菜单</span>
            </button>
          </div>
        </div>
        <div className={`mobile-panel ${mobileOpen ? "open" : ""}`}>
          <button className={page === "home" ? "active" : ""} onClick={() => go("home")}><i className="fa fa-home" /> 首页</button>
          <button className={page === "image" ? "active" : ""} onClick={() => go("image")}><i className="fa fa-image" /> 图像鉴伪</button>
          <button className={page === "video" ? "active" : ""} onClick={() => go("video")}><i className="fa fa-film" /> 视频鉴伪</button>
          <button className={page === "retrieve" ? "active" : ""} onClick={() => go("retrieve")}><i className="fa fa-search" /> 侵权检索</button>
          <button onClick={() => { window.location.href = "/v2/"; }}><i className="fa fa-bolt" /> V2 Agent</button>
          <button className={page === "history" ? "active" : ""} onClick={() => go("history")}><i className="fa fa-clock-o" /> 历史记录</button>
          <button className={page === "developer" ? "active" : ""} onClick={() => go("developer")}><i className="fa fa-code" /> 开发者平台</button>
          <button onClick={authAction}><i className={`fa ${user ? "fa-sign-out" : "fa-user"}`} /> {user ? "退出登录" : "登录/注册"}</button>
        </div>
      </header>
      <nav className="mobile-bottom-nav">
        <button className={page === "home" ? "active" : ""} onClick={() => go("home")}><i className="fa fa-home" /><span>首页</span></button>
        <button className={page === "image" ? "active" : ""} onClick={() => go("image")}><i className="fa fa-image" /><span>图像</span></button>
        <button className={page === "video" ? "active" : ""} onClick={() => go("video")}><i className="fa fa-film" /><span>视频</span></button>
        <button className={page === "retrieve" ? "active" : ""} onClick={() => go("retrieve")}><i className="fa fa-search" /><span>检索</span></button>
        <button className={page === "history" ? "active" : ""} onClick={() => go("history")}><i className="fa fa-clock-o" /><span>历史</span></button>
        <button className={page === "developer" ? "active" : ""} onClick={() => go("developer")}><i className="fa fa-code" /><span>开发</span></button>
      </nav>
    </>
  );
}

function HomePage({ counters, setPage }: { counters: Counters; setPage: (page: PageKey) => void }) {
  const [slide, setSlide] = useState(0);
  const slides = [
    {
      image: "/system/AIGC.jpg",
      tag: "多模态AI检测平台",
      title: "RealGuard\n智能内容检测",
      desc: "覆盖图像、视频多模态内容检测与检索，从源头发现疑似侵权内容，保障数字内容安全。",
      action: "开始检测",
      icon: "fa-rocket",
      page: "image" as PageKey,
      className: "slide-1"
    },
    {
      image: "/system/index1.jpg",
      tag: "深度学习驱动",
      title: "图像侵权\n智能检测",
      desc: "基于前沿深度学习算法，精准识别AI生成图像、PS篡改等疑似侵权内容。",
      action: "图像鉴伪",
      icon: "fa-image",
      page: "image" as PageKey,
      className: "slide-2"
    },
    {
      image: "/system/index3.jpg",
      tag: "海量数据检索",
      title: "多模态\n侵权检索",
      desc: "检索疑似侵权的图像，检索疑似侵权的视频，在海量数据库中快速定位相似可疑内容。",
      action: "开始检索",
      icon: "fa-search",
      page: "retrieve" as PageKey,
      className: "slide-3"
    }
  ];

  useEffect(() => {
    const timer = window.setInterval(() => setSlide((value) => (value + 1) % slides.length), 6000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <>
      <section className="hero-section">
        <div className="hero-container">
          <div className="carousel-wrapper">
            <div className="carousel-track" style={{ transform: `translateX(-${slide * 100}%)` }}>
              {slides.map((item, index) => (
                <div className={`carousel-slide ${item.className} ${slide === index ? "active" : ""}`} key={item.title}>
                  <img src={item.image} alt={item.tag} />
                  <div className="carousel-overlay">
                    <div className="hero-content">
                      <div className="hero-tag">
                        <span className="dot" />
                        {item.tag}
                      </div>
                      <h2 className="hero-title">{item.title}</h2>
                      <p className="hero-desc">{item.desc}</p>
                      <button className="hero-btn" onClick={() => setPage(item.page)}>
                        <i className={`fa ${item.icon}`} /> {item.action}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <button className="carousel-nav-btn prev" onClick={() => setSlide((slide + slides.length - 1) % slides.length)}>
              <i className="fa fa-chevron-left" />
            </button>
            <button className="carousel-nav-btn next" onClick={() => setSlide((slide + 1) % slides.length)}>
              <i className="fa fa-chevron-right" />
            </button>
            <div className="carousel-indicators">
              {slides.map((item, index) => (
                <button
                  aria-label={`切换到第 ${index + 1} 张`}
                  className={`carousel-dot ${slide === index ? "active" : ""}`}
                  key={item.tag}
                  onClick={() => setSlide(index)}
                />
              ))}
            </div>
            <div className="carousel-progress">
              <div className="carousel-progress-bar" key={slide} />
            </div>
          </div>
        </div>
      </section>

      <section className="section skill-entry-section">
        <div className="container">
          <SkillEntryPanel />
        </div>
      </section>

      <section className="section section-alt">
        <div className="container">
          <SectionHeader title="核心功能" desc="图像鉴伪、视频侵权检测、图像侵权检索、视频侵权检索，并提供独立 V2 Agent 体验入口" />
          <div className="features-grid">
            <FeatureCard accent="var(--primary)" icon="fa-image" title="图像鉴伪" desc="基于深度学习的图像真伪识别，支持多种场景的AIGC图像检测" onClick={() => setPage("image")} />
            <FeatureCard accent="var(--warning)" icon="fa-film" title="视频鉴伪" desc="针对视频内容的 AI 生成检测与篡改识别，帧级分析定位可疑片段。" onClick={() => setPage("video")} />
            <FeatureCard accent="var(--accent)" icon="fa-search" title="图像侵权检索" desc="检索疑似侵权的图像，在图像数据库中快速定位可疑图像内容。" onClick={() => setPage("retrieve")} />
            <FeatureCard accent="#8b5cf6" icon="fa-play-circle" title="视频侵权检索" desc="检索疑似侵权的视频，在数据库中快速定位相似可疑视频内容。" onClick={() => setPage("retrieve")} />
            <FeatureCard accent="var(--primary-dark)" icon="fa-bolt" title="V2 鉴伪 Agent" desc="独立新版系统，使用 qwen3-vl-flash 融合 ELA、噪声残差等取证证据。" onClick={() => { window.location.href = "/v2/"; }} />
          </div>
        </div>
      </section>

      <section className="section section-default">
        <div className="container">
          <SectionHeader title="识别示例" desc="点击案例查看简洁检测结果" />
          <div className="examples-grid">
            <ExampleCard image="/system/case1.webp" title="案例一：泳池场景人物图像" desc="综合判断为 AI 生成（53.8%），点击查看检测结果。" real={46.2} fake={53.8} />
            <ExampleCard image="/system/case2.webp" title="案例二：几何色块人像图像" desc="综合判断为 AI 生成（73.9%），点击查看检测结果。" real={26.1} fake={73.9} />
          </div>
        </div>
      </section>

      <section className="section section-alt">
        <div className="container">
          <div className="stats-grid">
            <Stat value="99.8%" label="识别准确率" />
            <Stat value={String(counters.image_detect + counters.video_detect || 10000) + "+"} label="检测任务" />
            <Stat value={String(counters.image_retrieve + counters.video_retrieve || 5000000) + "+"} label="检索任务" />
            <Stat value="0.3s" label="平均响应时间" />
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

function FeatureCard({ accent, icon, title, desc, onClick }: { accent: string; icon: string; title: string; desc: string; onClick: () => void }) {
  return (
    <button className="feature-card fade-up visible" style={{ "--card-accent": accent } as React.CSSProperties} onClick={onClick}>
      <div className="feature-icon" style={{ background: colorBg(accent), color: accent }}>
        <i className={`fa ${icon}`} />
      </div>
      <h3>{title}</h3>
      <p>{desc}</p>
      <span className="feature-link">
        进入功能 <i className="fa fa-arrow-right" />
      </span>
    </button>
  );
}

function SkillEntryPanel() {
  return (
    <div className="skill-entry-panel fade-up visible">
      <div className="skill-entry-main">
        <div className="skill-entry-badges">
          <span><i className="fa fa-plug" /> SKILL 已介入</span>
          <span>OpenClaw / AI Agent 可调用</span>
        </div>
        <h3>$realguard-forensics 是给外部 Agent 的公开鉴伪入口</h3>
        <p>
          必须做成公开 skill 的原因很直接：OpenClaw 等外部 agent 访问不到你的本地仓库路径，也不知道接口字段、报告字段和解释边界。
          让它读取公网 <code>{REALGUARD_SKILL_URL}</code> 后，就能直接调用公开 V2 API 或仓库 CLI，稳定输出可审计的鉴伪结论。
        </p>
        <div className="skill-flow">
          <span>读取公网 Skill</span>
          <i className="fa fa-arrow-right" />
          <span>调用 V2 API / CLI</span>
          <i className="fa fa-arrow-right" />
          <span>返回带证据的结论</span>
        </div>
      </div>
      <div className="skill-entry-code">
        <label>公开 Skill URL</label>
        <code>{REALGUARD_SKILL_URL}</code>
        <label>给 OpenClaw 的一句话</label>
        <code>{REALGUARD_SKILL_HANDOFF}</code>
        <label>CLI 命令</label>
        <code>{REALGUARD_SKILL_COMMAND}</code>
        <button onClick={() => { window.location.href = "/v2/"; }}>
          进入 V2 Agent <i className="fa fa-arrow-right" />
        </button>
        <button onClick={() => { window.location.href = "/?page=developer"; }}>
          打开开发者平台 <i className="fa fa-code" />
        </button>
      </div>
    </div>
  );
}

function DeveloperPlatformPage() {
  const [token, setToken] = useState("");
  const [fileType, setFileType] = useState("image");
  const [testFile, setTestFile] = useState<File | null>(null);
  const [consoleBusy, setConsoleBusy] = useState(false);
  const [consoleStatus, setConsoleStatus] = useState<Status>(null);
  const [consoleResult, setConsoleResult] = useState<Record<string, unknown> | null>(null);
  const [consoleMeta, setConsoleMeta] = useState<{ endpoint: string; elapsedMs: number; at: string } | null>(null);
  const docsNavGroups = [
    {
      title: "开始使用",
      links: [
        ["#overview", "总览"],
        ["#quickstart", "快速开始"],
        ["#auth", "认证"],
      ],
    },
    {
      title: "API Reference",
      links: [
        ["#reference", "接口总览"],
        ["#detect", "Detect"],
        ["#forensics", "Forensics"],
        ["#provenance", "Provenance"],
        ["#reports", "Reports"],
        ["#errors", "错误码"],
      ],
    },
    {
      title: "开发工具",
      links: [
        ["#examples", "代码示例"],
        ["#console", "在线测试台"],
        ["#agent-fields", "Agent 字段"],
      ],
    },
    {
      title: "资源",
      links: [
        ["#enterprise", "企业接入"],
        ["#resources", "公开资源"],
      ],
    },
  ];
  const endpoints = [
    { method: "GET", path: "/health", title: "Health", desc: "服务状态、模型状态、访问保护状态。", anchor: "#health" },
    { method: "POST", path: "/detect", title: "Detect", desc: "核心鉴伪接口。multipart 上传 file，可选 fileType。", anchor: "#detect" },
    { method: "POST", path: "/forensics", title: "Forensics", desc: "图像可解释性取证分析，返回 ELA、噪声、频域等证据。", anchor: "#forensics" },
    { method: "POST", path: "/provenance", title: "Provenance", desc: "图像 C2PA / SynthID / 内容凭证验证。", anchor: "#provenance" },
    { method: "GET", path: "/report/{reportId}/download", title: "Report Download", desc: "下载 HTML 或结构化报告，用于审计归档。", anchor: "#reports" },
  ];
  const requestParams = [
    ["file", "File", "required", "待检测文件。支持图片、视频、音频、文档；用 multipart/form-data 上传。"],
    ["fileType", "string", "optional", "文件类型提示：image、video、audio、document。未传时服务会尝试自动推断。"],
  ];
  const reportPathParams = [
    ["reportId", "string", "required", "检测结果返回的报告编号，例如 RJ-RPT-20260602-0001。"],
  ];
  const fields = [
    ["agentSummary", "给 AI agent 优先使用的结构化摘要。"],
    ["verdict", "鉴伪结论，例如 real / suspected / likely_ai_generated / unknown。"],
    ["confidence", "0-1 置信度，展示时可换算百分比。"],
    ["modelVersion", "模型或规则链路版本。"],
    ["cacheVersion", "分析缓存版本，用于判断结果是否来自同一分析逻辑。"],
    ["source", "vlm / mock / heuristic 等，决定结果可信度说明。"],
    ["reportId", "可用于下载和归档报告的编号。"],
    ["synthid / visibleWatermark", "水印、SynthID、可见水印等附加证据。"],
  ];
  const errorRows = [
    ["400", "Bad Request", "缺少 file、fileType 不合法或 multipart 格式错误。"],
    ["401", "Unauthorized", "服务启用访问保护，但未传 Token 或 Token 无效。"],
    ["413", "Payload Too Large", "文件超过服务允许大小，需要压缩或走异步/分片流程。"],
    ["422", "Unprocessable Entity", "文件格式无法识别或不支持当前检测链路。"],
    ["500", "Internal Server Error", "服务端分析失败；记录 taskId 并重试或转人工处理。"],
  ];
  const jsExample = `const form = new FormData();
form.append("file", fileInput.files[0]);
form.append("fileType", "image");

const res = await fetch("${REALGUARD_API_BASE}/detect", {
  method: "POST",
  headers: { "X-Jianzhen-Token": token },
  body: form
});
const data = await res.json();
console.log(data.agentSummary || data);`;
  const curlDetectExample = `curl -fsS -X POST ${REALGUARD_API_BASE}/detect \\
  -H "X-Jianzhen-Token: <token>" \\
  -F "file=@/path/to/file.png" \\
  -F "fileType=image"`;
  const curlHealthExample = `curl -fsS ${REALGUARD_API_BASE}/health`;
  const curlForensicsExample = `curl -fsS -X POST ${REALGUARD_API_BASE}/forensics \\
  -F "file=@/path/to/image.png"`;
  const curlProvenanceExample = `curl -fsS -X POST ${REALGUARD_API_BASE}/provenance \\
  -F "file=@/path/to/image.png"`;
  const curlReportExample = `curl -fsS ${REALGUARD_API_BASE}/report/<reportId>/download \\
  -o realguard-report.html`;
  const pythonExample = `import requests

url = "${REALGUARD_API_BASE}/detect"
headers = {"X-Jianzhen-Token": token}  # optional
with open("/path/to/file.png", "rb") as f:
    r = requests.post(url, headers=headers, files={"file": f}, data={"fileType": "image"})
r.raise_for_status()
print(r.json().get("agentSummary") or r.json())`;
  const cliExample = `python3 scripts/realguard_cli.py detect /path/to/file \\
  --base-url http://124.222.3.205 \\
  --api-prefix /v2-api \\
  --pretty`;

  const runHealthCheck = async () => {
    const started = performance.now();
    setConsoleBusy(true);
    setConsoleStatus({ tone: "info", text: "正在检查 V2 API 状态..." });
    try {
      const result = await getV2Health(token);
      setConsoleResult(result as Record<string, unknown>);
      setConsoleMeta({ endpoint: "GET /health", elapsedMs: Math.round(performance.now() - started), at: new Date().toLocaleString() });
      setConsoleStatus({ tone: "ok", text: "健康检查成功。" });
    } catch (error) {
      setConsoleStatus({ tone: "error", text: error instanceof Error ? error.message : "健康检查失败" });
    } finally {
      setConsoleBusy(false);
    }
  };

  const runDetectTest = async () => {
    if (!testFile) {
      setConsoleStatus({ tone: "error", text: "请先选择要测试的文件。" });
      return;
    }
    const started = performance.now();
    setConsoleBusy(true);
    setConsoleStatus({ tone: "info", text: "正在上传文件并调用鉴伪 API..." });
    try {
      const result = await runV2Detect({ file: testFile, fileType, token });
      setConsoleResult(result as Record<string, unknown>);
      setConsoleMeta({ endpoint: "POST /detect", elapsedMs: Math.round(performance.now() - started), at: new Date().toLocaleString() });
      setConsoleStatus({ tone: "ok", text: `检测完成：${result.verdict || "已返回结果"}` });
    } catch (error) {
      setConsoleStatus({ tone: "error", text: error instanceof Error ? error.message : "检测失败" });
    } finally {
      setConsoleBusy(false);
    }
  };

  const renderedResult = consoleResult ? JSON.stringify(consoleResult, null, 2) : "";

  return (
    <main className="main developer-docs-page">
      <div className="container developer-platform docs-platform">
        <div className="docs-shell">
          <aside className="docs-sidebar" aria-label="开发者文档目录">
            <a className="docs-brand" href="#overview">
              <span><i className="fa fa-shield" /></span>
              <div>
                <strong>RealGuard API</strong>
                <small>Developer Docs</small>
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
              <span>Base URL</span>
              <code>{REALGUARD_API_BASE}</code>
            </div>
          </aside>

          <article className="docs-main">
            <section id="overview" className="docs-section docs-hero-section">
              <div className="docs-hero-copy">
                <div className="developer-badges">
                  <span>Public API</span>
                  <span>Agent Skill</span>
                  <span>Multipart Upload</span>
                </div>
                <h1>RealGuard Developer API</h1>
                <p>
                  面向 OpenClaw、外部 AI Agent 和业务系统的鉴伪接口文档。
                  这里提供公开 Skill、Base URL、认证方式、请求参数、响应字段、错误处理和在线测试台。
                </p>
                <div className="docs-hero-actions">
                  <a href="#quickstart">开始调用</a>
                  <a href="#console" className="secondary">在线测试 API</a>
                </div>
              </div>
              <div className="docs-info-card">
                <label>API Base URL</label>
                <code>{REALGUARD_API_BASE}</code>
                <label>Public Skill URL</label>
                <code>{REALGUARD_SKILL_URL}</code>
                <label>Full Markdown Docs</label>
                <code>{REALGUARD_API_DOC_URL}</code>
              </div>
            </section>

            <section id="quickstart" className="docs-section">
              <div className="docs-section-kicker">Getting Started</div>
              <h2>快速开始</h2>
              <p className="docs-lead">
                第一次接入只需要三步：读取公开 Skill、上传文件调用 <code>POST /detect</code>、按
                <code>agentSummary</code>、<code>verdict</code>、<code>confidence</code> 和 <code>reportId</code> 输出结论。
              </p>
              <div className="docs-callout docs-callout-strong">
                <h3>为什么必须公开 Skill</h3>
                <p>
                  别的 agent 访问不到你的本地路径，也无法猜测接口字段、报告下载地址和解释边界。
                  公开 Skill 后，OpenClaw 只要读取公网 URL，就能稳定调用 API，并知道哪些字段必须带入最终鉴伪结论。
                </p>
                <pre>{REALGUARD_SKILL_HANDOFF}</pre>
              </div>
              <div className="docs-code-block">
                <div className="docs-code-title"><span className="method method-post">POST</span><span>首次调用 /detect</span></div>
                <pre>{curlDetectExample}</pre>
              </div>
            </section>

            <section id="auth" className="docs-section">
              <div className="docs-section-kicker">Authentication</div>
              <h2>认证</h2>
              <p className="docs-lead">
                当前公开环境可用于演示；如果服务开启访问保护，请在任一请求头中传入令牌。
              </p>
              <div className="docs-code-block compact">
                <pre>{`X-Jianzhen-Token: <token>
Authorization: Bearer <token>`}</pre>
              </div>
              <div className="docs-callout">
                <strong>安全建议</strong>
                <p>Token 不要写进前端源码或公开仓库。自动化 agent 应使用作用域受限的令牌，并记录调用人、时间和文件摘要。</p>
              </div>
            </section>

            <section id="reference" className="docs-section">
              <div className="docs-section-kicker">API Reference</div>
              <h2>接口总览</h2>
              <p className="docs-lead">所有接口都基于 <code>{REALGUARD_API_BASE}</code>。上传接口使用 <code>multipart/form-data</code>。</p>
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
                <p>检查 V2 API 可用性、模型链路状态和访问保护状态。适合作为部署探针和集成前连通性检查。</p>
                <div className="docs-code-block compact"><pre>{curlHealthExample}</pre></div>
              </div>

              <div id="detect" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/detect</h3>
                </div>
                <p>核心鉴伪接口。上传文件后返回任务编号、鉴伪结论、置信度、证据摘要、模型版本和报告编号。</p>
                <h4>Request body</h4>
                <div className="docs-table docs-table-4">
                  <strong>字段</strong><strong>类型</strong><strong>是否必填</strong><strong>说明</strong>
                  {requestParams.map(([name, type, required, desc]) => (
                    <Fragment key={name}>
                      <code>{name}</code><span>{type}</span><span>{required}</span><p>{desc}</p>
                    </Fragment>
                  ))}
                </div>
                <h4>Response fields</h4>
                <div className="docs-table docs-table-2">
                  <strong>字段</strong><strong>说明</strong>
                  {fields.map(([field, desc]) => (
                    <Fragment key={field}>
                      <code>{field}</code><p>{desc}</p>
                    </Fragment>
                  ))}
                </div>
                <div className="docs-code-block compact"><pre>{curlDetectExample}</pre></div>
              </div>

              <div id="forensics" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/forensics</h3>
                </div>
                <p>针对图像返回更细的取证证据，例如 ELA、噪声一致性、频域异常、边缘异常和可解释性摘要。</p>
                <div className="docs-code-block compact"><pre>{curlForensicsExample}</pre></div>
              </div>

              <div id="provenance" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/provenance</h3>
                </div>
                <p>验证 C2PA、SynthID、可见水印和内容凭证信号。适合与 <code>/detect</code> 的模型结论合并展示。</p>
                <div className="docs-code-block compact"><pre>{curlProvenanceExample}</pre></div>
              </div>

              <div id="reports" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-get">GET</span>
                  <h3>/report/{"{reportId}"}/download</h3>
                </div>
                <p>根据检测返回的 <code>reportId</code> 下载报告，用于外部系统归档、审计或人工复核。</p>
                <div className="docs-table docs-table-4">
                  <strong>路径参数</strong><strong>类型</strong><strong>是否必填</strong><strong>说明</strong>
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
              <h2>错误码</h2>
              <p className="docs-lead">Agent 不应该吞掉 API 错误。4xx 通常是请求或权限问题，5xx 应记录上下文后重试或降级。</p>
              <div className="docs-table docs-table-3">
                <strong>HTTP</strong><strong>名称</strong><strong>处理方式</strong>
                {errorRows.map(([code, name, desc]) => (
                  <Fragment key={code}>
                    <code>{code}</code><span>{name}</span><p>{desc}</p>
                  </Fragment>
                ))}
              </div>
            </section>

            <section id="examples" className="docs-section">
              <div className="docs-section-kicker">Examples</div>
              <h2>代码示例</h2>
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
              </div>
            </section>

            <section id="console" className="docs-section docs-console-section">
              <div className="docs-section-kicker">API Console</div>
              <h2>在线 API 测试台</h2>
              <p className="docs-lead">在网站内直接测试健康检查和鉴伪上传，验证 Token、接口连通性、响应字段和耗时。</p>
              <div className="console-layout">
                <div className="console-controls">
                  <label>
                    访问令牌（可选）
                    <input
                      type="password"
                      placeholder="X-Jianzhen-Token"
                      value={token}
                      onChange={(event) => setToken(event.target.value)}
                    />
                  </label>
                  <label>
                    文件类型
                    <select value={fileType} onChange={(event) => setFileType(event.target.value)}>
                      <option value="image">image</option>
                      <option value="video">video</option>
                      <option value="audio">audio</option>
                      <option value="document">document</option>
                    </select>
                  </label>
                  <label>
                    测试文件
                    <input
                      type="file"
                      accept="image/*,video/*,audio/*,.txt,.pdf,.doc,.docx,.md"
                      onChange={(event) => setTestFile(event.target.files?.[0] || null)}
                    />
                  </label>
                  <div className="console-actions">
                    <button disabled={consoleBusy} onClick={runHealthCheck}>
                      <i className={`fa ${consoleBusy ? "fa-spinner detect-spin" : "fa-heartbeat"}`} /> 健康检查
                    </button>
                    <button disabled={consoleBusy || !testFile} onClick={runDetectTest}>
                      <i className={`fa ${consoleBusy ? "fa-spinner detect-spin" : "fa-play"}`} /> 运行鉴伪测试
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
                    <span>响应 JSON</span>
                    {renderedResult && (
                      <button onClick={() => navigator.clipboard?.writeText(renderedResult)}>复制</button>
                    )}
                  </div>
                  <pre>{renderedResult || "运行健康检查或鉴伪测试后，响应 JSON 会显示在这里。"}</pre>
                </div>
              </div>
            </section>

            <section id="agent-fields" className="docs-section">
              <div className="docs-section-kicker">Agent Output</div>
              <h2>Agent 应读取的关键字段</h2>
              <p className="docs-lead">外部 agent 输出时至少包含结论、置信度、证据摘要、版本和报告编号，避免只输出一句“真假”。</p>
              <div className="developer-fields">
                {fields.map(([field, desc]) => (
                  <div key={field}>
                    <code>{field}</code>
                    <p>{desc}</p>
                  </div>
                ))}
              </div>
            </section>

            <section id="enterprise" className="docs-section">
              <div className="docs-section-kicker">Enterprise</div>
              <h2>企业接入标准</h2>
              <div className="developer-fields">
                <div><code>版本固定</code><p>记录 <code>modelVersion</code> 与 <code>cacheVersion</code>，避免不同分析版本混用。</p></div>
                <div><code>审计留痕</code><p>保存原始 JSON、<code>taskId</code>、<code>reportId</code>、文件摘要和调用时间。</p></div>
                <div><code>错误处理</code><p>对 4xx 展示请求问题，对 5xx 做重试或降级；不要吞掉 API 错误。</p></div>
                <div><code>结论约束</code><p>输出必须包含置信度和限制说明，不得把检测结果表述为绝对证明。</p></div>
              </div>
            </section>

            <section id="resources" className="docs-section docs-resource-section">
              <div className="docs-section-kicker">Resources</div>
              <h2>公开资源</h2>
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
              </div>
              <div className="docs-callout">
                <strong>解释边界</strong>
                <p>
                  API 结果是鉴伪证据，不是绝对证明。Agent 必须说明 <code>source</code>、<code>modelVersion</code> 和
                  <code>cacheVersion</code>；若 source 为 mock、heuristic 或回退链路，应明确标注限制。
                </p>
              </div>
            </section>
          </article>
        </div>
      </div>
    </main>
  );
}

function ExampleCard({ image, title, desc, real, fake }: { image: string; title: string; desc: string; real: number; fake: number }) {
  return (
    <div className="example-card fade-up visible">
      <div className="example-img">
        <img src={image} alt={title} />
        <span className="example-badge fake">伪造图像</span>
      </div>
      <div className="example-body">
        <h3>{title}</h3>
        <p>{desc}</p>
        <Progress label="图片为真" value={real} tone="green" />
        <Progress label="图片为假" value={fake} tone="red" />
      </div>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat-card fade-up visible">
      <div className="stat-val">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function ImageDetectionPage({
  isGuest,
  guestDetections,
  onNeedAuth,
  onDone
}: {
  isGuest: boolean;
  guestDetections: number;
  onNeedAuth: () => void;
  onDone: () => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [result, setResult] = useState<ImageDetectionResult | null>(null);
  const [status, setStatus] = useState<Status>({ tone: "info", text: "等待上传图片..." });
  const [busy, setBusy] = useState(false);

  function selectFile(next: File | null) {
    setFile(next);
    setResult(null);
    setPreview(next ? URL.createObjectURL(next) : "");
    setStatus({ tone: "info", text: next ? `已选择: ${next.name}` : "等待上传图片..." });
  }

  async function submit() {
    if (!file) {
      setStatus({ tone: "error", text: "请先选择图片" });
      return;
    }
    if (isGuest && guestDetections >= 1) {
      setStatus({ tone: "info", text: "访客免费检测次数已用完，请登录后继续检测" });
      onNeedAuth();
      return;
    }
    setBusy(true);
    setStatus({ tone: "info", text: "正在分析图像……" });
    try {
      const data = await detectImage(file);
      setResult(data.result);
      setStatus({ tone: "ok", text: "检测完成" });
      await onDone();
    } catch (error) {
      setStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function detectSample(sample: { image: string; title: string }) {
    if (isGuest && guestDetections >= 1) {
      setStatus({ tone: "info", text: "访客免费检测次数已用完，请登录后继续检测" });
      onNeedAuth();
      return;
    }
    setBusy(true);
    setResult(null);
    setStatus({ tone: "info", text: `正在加载示例图片：${sample.title}` });
    try {
      const response = await fetch(sample.image);
      if (!response.ok) {
        throw new Error(`示例图片加载失败：${response.status}`);
      }
      const blob = await response.blob();
      const ext = sample.image.split(".").pop()?.split("?")[0] || "jpg";
      const sampleFile = new File([blob], `${sample.title}.${ext}`, {
        type: blob.type || "image/jpeg"
      });
      setFile(sampleFile);
      setPreview(URL.createObjectURL(sampleFile));
      setStatus({ tone: "info", text: "正在分析示例图片……" });
      const data = await detectImage(sampleFile);
      setResult(data.result);
      setStatus({ tone: "ok", text: "示例图片检测完成" });
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
        <PageHeader icon="fa-image" title="图像鉴伪" desc="上传图片或选择示例图片，使用AI模型检测疑似侵权内容" />
        <div className="layout">
          <div className="card">
            <div className="section-label"><i className="fa fa-cogs" /> 选择检测模型</div>
            <div className="model-tabs">
              <button className="model-tab active"><i className="fa fa-magic" /> AIGC检测</button>
              <button className="model-tab"><i className="fa fa-paint-brush" /> PS篡改检测</button>
            </div>
            <div className="model-desc"><strong>AIGC检测：</strong>基于检测器快速判定AI生成概率，并结合元数据做辅助展示。</div>
            <div className="card-divider" />
            <div className="section-label"><i className="fa fa-upload" /> 上传图片</div>
            {isGuest && <TrialHint used={guestDetections} />}
            <UploadBox accept="image/*" file={file} preview={preview} onFile={selectFile} kind="图片" />
            <button className={`btn-primary ${busy ? "detecting" : ""}`} disabled={!file || busy} onClick={submit}>
              <i className={`fa ${busy ? "fa-circle-o-notch detect-spin" : "fa-search"}`} /> {busy ? "Agent正在分析" : "开始检测"}
            </button>
          </div>
          <div className="card">
            <div className="section-label"><i className="fa fa-info-circle" /> 当前状态</div>
            <StatusRow status={status} busy={busy} />
            <div className="card-divider" />
            {result ? <ImageResult result={result} /> : <ImageSamples onSelect={detectSample} busy={busy} />}
          </div>
        </div>
      </div>
    </main>
  );
}

function VideoDetectionPage({
  isGuest,
  guestDetections,
  onNeedAuth,
  onDone
}: {
  isGuest: boolean;
  guestDetections: number;
  onNeedAuth: () => void;
  onDone: () => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [result, setResult] = useState<VideoDetectionResult | null>(null);
  const [status, setStatus] = useState<Status>({ tone: "info", text: "等待上传视频或填写URL..." });
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!file && !videoUrl.trim()) {
      setStatus({ tone: "error", text: "请上传视频或填写视频 URL" });
      return;
    }
    if (isGuest && guestDetections >= 1) {
      setStatus({ tone: "info", text: "访客免费检测次数已用完，请登录后继续检测" });
      onNeedAuth();
      return;
    }
    setBusy(true);
    setStatus({ tone: "info", text: "正在分析视频帧与编码特征…" });
    try {
      const data = await detectVideo({ file: file || undefined, videoUrl, fastMode: true });
      setResult(data.result);
      setStatus({ tone: "ok", text: "检测完成" });
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
        <PageHeader icon="fa-film" title="视频检测" desc="上传本地视频或输入URL，使用AI模型检测视频真伪" />
        <div className="layout">
          <div className="card">
            <div className="section-label"><i className="fa fa-upload" /> 上传视频</div>
            {isGuest && <TrialHint used={guestDetections} />}
            <UploadBox accept="video/*" file={file} onFile={setFile} kind="视频" />
            <div className="url-or">或</div>
            <div className="section-label"><i className="fa fa-link" /> 输入视频URL</div>
            <div className="url-input-wrap">
              <input className="url-input" value={videoUrl} onChange={(event) => setVideoUrl(event.target.value)} placeholder="https://example.com/video.mp4" />
            </div>
            <button className={`btn-primary ${busy ? "detecting" : ""}`} disabled={busy || (!file && !videoUrl.trim())} onClick={submit}>
              <i className={`fa ${busy ? "fa-spinner detect-spin" : "fa-search"}`} /> {busy ? "检测中…" : "开始检测"}
            </button>
          </div>
          <div className="card">
            <div className="section-label"><i className="fa fa-info-circle" /> 当前状态</div>
            <StatusRow status={status} busy={busy} />
            <div className="card-divider" />
            {result ? <VideoResult result={result} /> : <VideoSamples />}
          </div>
        </div>
      </div>
    </main>
  );
}

function RetrievePage({ onDone }: { onDone: () => Promise<void> }) {
  const [searchType, setSearchType] = useState<"image" | "video">("image");
  const [file, setFile] = useState<File | null>(null);
  const [libraries, setLibraries] = useState<string[]>([]);
  const [dataset, setDataset] = useState("");
  const [topK, setTopK] = useState(50);
  const [results, setResults] = useState<RetrieveItem[]>([]);
  const [baseUrl, setBaseUrl] = useState("");
  const [status, setStatus] = useState<Status>({ tone: "info", text: "等待上传图片..." });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getLibraries(searchType)
      .then((data) => {
        setLibraries(data.libraries || []);
        setDataset(data.selected || data.libraries?.[0] || "");
      })
      .catch((error) => setStatus({ tone: "error", text: errorMessage(error) }));
  }, [searchType]);

  async function submit() {
    if (!file) {
      setStatus({ tone: "error", text: "请先上传查询文件" });
      return;
    }
    setBusy(true);
    setStatus({ tone: "info", text: "正在检索可疑内容..." });
    try {
      const data = await retrieveSearch({ file, searchType, dataset, topK });
      setResults(data.results || []);
      setBaseUrl(data.base_url || "");
      setStatus({ tone: "ok", text: `检索完成，共 ${data.results?.length || 0} 条可疑结果` });
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
        <PageHeader icon="fa-search" title={searchType === "image" ? "图像侵权检索" : "视频侵权检索"} desc="在数据库中检索视觉相似的可疑内容" />
        <div className="layout">
          <div className="card">
            <div className="section-label"><i className="fa fa-upload" /> 文件上传</div>
            <UploadBox accept={searchType === "image" ? "image/*" : "video/*"} file={file} onFile={setFile} kind={searchType === "image" ? "图片" : "视频"} />
            <button className="btn-primary" disabled={!file || busy} onClick={submit}><i className="fa fa-search" /> 开始检索</button>
          </div>
          <div className="card">
            <div className="section-label"><i className="fa fa-sliders" /> 检索参数</div>
            <label className="param-label">检索类型</label>
            <select className="param-select" value={searchType} onChange={(event) => setSearchType(event.target.value as "image" | "video")}>
              <option value="image">图像侵权检索</option>
              <option value="video">视频侵权检索</option>
            </select>
            <label className="param-label">检索库</label>
            <select className="param-select" value={dataset} onChange={(event) => setDataset(event.target.value)}>
              {libraries.length ? libraries.map((item) => <option key={item} value={item}>{item}</option>) : <option value="">无可用检索库</option>}
            </select>
            <label className="param-label">返回数量</label>
            <select className="param-select" value={topK} onChange={(event) => setTopK(Number(event.target.value))}>
              <option value={5}>Top 5</option>
              <option value={10}>Top 10</option>
              <option value={20}>Top 20</option>
              <option value={50}>Top 50</option>
            </select>
            <div className="status-box">
              <div className="status-label"><i className="fa fa-info-circle" /> 状态信息</div>
              <div className="status-text">{status?.text}</div>
            </div>
          </div>
        </div>
        {results.length > 0 && <RetrieveResults results={results} baseUrl={baseUrl} searchType={searchType} />}
      </div>
    </main>
  );
}

function HistoryPage({ setPage }: { setPage: (page: PageKey) => void }) {
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
  const historyRequestIdRef = useRef(0);

  async function loadHistoryRecords(
    targetTab: HistoryTabKey,
    { preserveOnError = false, append = false, reset = false }: { preserveOnError?: boolean; append?: boolean; reset?: boolean } = {},
  ) {
    const requestId = historyRequestIdRef.current + 1;
    historyRequestIdRef.current = requestId;
    setStatus({ tone: "info", text: "正在加载历史记录" });
    setHistoryBusy(true);
    const activeFilter = isHistoryFilterSupported(targetTab, filter) ? filter : "all";
    const offset = append ? records.length : 0;
    const limit = append ? HISTORY_PAGE_SIZE : reset ? HISTORY_PAGE_SIZE : historyLimit;
    const request =
      targetTab === "image"
        ? getHistory("image-detections", { query, filter: activeFilter, limit, offset })
        : targetTab === "video"
          ? getHistory("video-detections", { query, filter: activeFilter, limit, offset })
          : getRetrievalHistory(targetTab === "imageRetrieve" ? "image" : "video", { query, filter: activeFilter, limit, offset });
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
  }, [tab, filter, query]);

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
        { label: "当前记录", value: historyFilterCounts.all ?? historyTotal, filterKey: "all" as HistoryFilterKey },
        { label: "访客记录", value: historyFilterCounts.guest ?? 0, filterKey: "guest" as HistoryFilterKey },
        { label: "带元数据", value: historyFilterCounts.metadata ?? 0, filterKey: "metadata" as HistoryFilterKey },
        { label: "有可疑点", value: historyFilterCounts.issues ?? 0, filterKey: "issues" as HistoryFilterKey },
      ];
    }
    if (tab === "video") {
      return [
        { label: "当前记录", value: historyFilterCounts.all ?? historyTotal, filterKey: "all" as HistoryFilterKey },
        { label: "访客记录", value: historyFilterCounts.guest ?? 0, filterKey: "guest" as HistoryFilterKey },
        { label: "AI结论", value: historyFilterCounts.ai ?? 0, filterKey: "ai" as HistoryFilterKey },
        { label: "真实结论", value: historyFilterCounts.real ?? 0, filterKey: "real" as HistoryFilterKey },
      ];
    }
    const resultCount = records.reduce((sum, record) => sum + Number(record.result_count || 0), 0);
    const topKAvg = records.length
      ? Math.round((records.reduce((sum, record) => sum + Number(record.top_k || 0), 0) / records.length) * 10) / 10
      : 0;
    return [
      { label: "当前查询", value: historyTotal || records.length },
      { label: "有命中", value: historyFilterCounts.hits ?? 0, filterKey: "hits" as HistoryFilterKey },
      { label: "无命中", value: historyFilterCounts.empty ?? 0, filterKey: "empty" as HistoryFilterKey },
      { label: "命中总数", value: resultCount },
      { label: "平均Top-K", value: topKAvg },
      { label: "查询类型", value: tab === "imageRetrieve" ? "图像" : "视频" },
    ];
  }, [historyFilterCounts, historyTotal, records, tab]);

  const filterOptions = getHistoryFilterOptions(tab);
  const activeSummary = getHistoryActiveSummary(tab, filter, query);
  const matchSummary =
    records.length === historyTotal
      ? `当前展示 ${records.length} 条记录`
      : `当前匹配 ${records.length} / ${historyTotal} 条记录`;

  async function copyCurrentView() {
    const url = window.location.href;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      window.prompt("复制当前历史视图链接", url);
    }
  }

  return (
    <main className="main">
      <div className="container">
        <PageHeader icon="fa-history" title="历史记录" desc="查看您的检测与检索历史记录" />
        <div className="card">
          <div className="model-tabs history-tabs">
            <button className={`model-tab ${tab === "image" ? "active" : ""}`} onClick={() => updateHistoryTab("image")}>图像鉴伪</button>
            <button className={`model-tab ${tab === "video" ? "active" : ""}`} onClick={() => updateHistoryTab("video")}>视频鉴伪</button>
            <button className={`model-tab ${tab === "imageRetrieve" ? "active" : ""}`} onClick={() => updateHistoryTab("imageRetrieve")}>图像检索</button>
            <button className={`model-tab ${tab === "videoRetrieve" ? "active" : ""}`} onClick={() => updateHistoryTab("videoRetrieve")}>视频检索</button>
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
                    placeholder={tab === "imageRetrieve" || tab === "videoRetrieve" ? "按文件名、命中库、首个命中、时间搜索历史记录" : "按文件名、结论、时间搜索历史记录"}
                  />
                </div>
                {query && (
                  <button type="button" className="btn-code history-search-clear" onClick={() => updateHistoryQuery("")}>
                    清空
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
                  {historyBusy ? "刷新中" : "刷新记录"}
                </button>
                <button
                  type="button"
                  className={`btn-code history-copy-btn ${
                    copied ? "history-copy-btn-copied" : ""
                  }`}
                  onClick={copyCurrentView}
                >
                  {copied ? "已复制视图链接" : "复制当前视图"}
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
                  重置条件
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
                  <HistoryRecords records={records} tab={tab} query={query} />
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
                        {historyBusy ? "加载中" : "加载更多"}
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <EmptyState
                  icon="fa-filter"
                  text="当前筛选条件下暂无记录"
                  actions={[
                    { label: "清除条件", onClick: () => { updateHistoryFilter("all"); updateHistoryQuery(""); } },
                    { label: tab === "video" ? "去视频鉴伪" : tab === "image" ? "去图像鉴伪" : "去侵权检索", onClick: () => setPage(tab === "video" ? "video" : tab === "image" ? "image" : "retrieve") },
                  ]}
                />
              )}
            </>
          ) : status?.tone === "error" ? (
            <EmptyState
              icon="fa-exclamation-triangle"
              text={status.text}
              actions={[
                { label: historyBusy ? "加载中" : "重试加载", onClick: () => { void loadHistoryRecords(tab); } },
                { label: tab === "video" ? "去视频鉴伪" : tab === "image" ? "去图像鉴伪" : "去侵权检索", onClick: () => setPage(tab === "video" ? "video" : tab === "image" ? "image" : "retrieve") },
              ]}
            />
          ) : !status && (filter !== "all" || query.trim()) ? (
            <EmptyState
              icon="fa-filter"
              text="当前筛选条件下暂无记录"
              actions={[
                { label: "清除条件", onClick: () => { updateHistoryFilter("all"); updateHistoryQuery(""); } },
                { label: tab === "video" ? "去视频鉴伪" : tab === "image" ? "去图像鉴伪" : "去侵权检索", onClick: () => setPage(tab === "video" ? "video" : tab === "image" ? "image" : "retrieve") },
              ]}
            />
          ) : !status && (
            <EmptyState
              icon="fa-clock-o"
              text="暂无记录"
              actions={[
                { label: "去图像鉴伪", onClick: () => setPage("image") },
                { label: "去视频鉴伪", onClick: () => setPage("video") },
                { label: "去侵权检索", onClick: () => setPage("retrieve") },
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

function TrialHint({ used }: { used: number }) {
  return (
    <div className="trial-note">
      <i className="fa fa-info-circle" />
      <span>{used >= 1 ? "访客检测次数已用完，登录后继续使用。" : "访客可免费完成 1 次检测，本次不会要求登录。"}</span>
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
  kind
}: {
  accept: string;
  file: File | null;
  preview?: string;
  onFile: (file: File | null) => void;
  kind: string;
}) {
  return (
    <div className="upload-area">
      <input accept={accept} type="file" id={`file-${kind}`} onChange={(event) => onFile(event.target.files?.[0] || null)} />
      {!file ? (
        <label htmlFor={`file-${kind}`} className="upload-placeholder">
          <div className="upload-icon"><i className="fa fa-cloud-upload" /></div>
          <div className="upload-text">拖放{kind}到此处，或点击上传</div>
          <div className="upload-hint">支持常见{kind}格式</div>
        </label>
      ) : (
        <div className="file-preview visible">
          {preview && <img src={preview} alt="预览" />}
          <div className="file-meta">
            <span>{file.name}</span><span>·</span><span>{formatSize(file.size)}</span><span className="file-badge">{kind}</span>
          </div>
          <button className="clear-btn" onClick={() => onFile(null)}><i className="fa fa-times" /> 清除</button>
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
  busy
}: {
  onSelect: (sample: { image: string; title: string }) => void;
  busy: boolean;
}) {
  return (
    <>
      <div className="section-label"><i className="fa fa-th-large" style={{ color: "var(--warning)" }} /> 示例图片 <span className="label-muted">点击直接检测</span></div>
      <div className="sample-list">
        <SampleItem image="/system/index1.jpg" title="示例图片 1" label="点击检测" neutral disabled={busy} onClick={onSelect} />
        <SampleItem image="/system/index2.jpg" title="示例图片 2" label="点击检测" neutral disabled={busy} onClick={onSelect} />
        <SampleItem image="/system/index3.jpg" title="示例图片 3" label="点击检测" neutral disabled={busy} onClick={onSelect} />
      </div>
      <div className="card-divider" />
      <Tips items={["AIGC检测：识别SD、DALL-E、Midjourney等AI生成图像", "PS篡改检测：识别拼接、修补、克隆等篡改痕迹", "结果包含概率、置信度与简洁结论"]} />
    </>
  );
}

function VideoSamples() {
  return (
    <>
      <div className="section-label"><i className="fa fa-th-large" style={{ color: "var(--warning)" }} /> 示例视频 <span className="label-muted">点击查看效果</span></div>
      <div className="sample-list">
        <SampleItem image="/system/video5227-cover.jpg" title="示例视频 1（video5227）" label="示例" fake play />
        <SampleItem image="/system/video189-cover.jpg" title="示例视频 2（video189）" label="示例" play />
        <SampleItem image="/system/video6785-cover.jpg" title="示例视频 3（video6785）" label="示例" fake play />
      </div>
      <div className="card-divider" />
      <Tips items={["支持本地文件上传和远程URL两种方式", "若文件和URL同时存在，优先使用本地文件", "检测结果包含AI/真实概率、置信度和说明"]} />
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
  onClick
}: {
  image: string;
  title: string;
  label: string;
  fake?: boolean;
  neutral?: boolean;
  play?: boolean;
  disabled?: boolean;
  onClick?: (sample: { image: string; title: string }) => void;
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
          <span className="sample-hint">查看 <i className="fa fa-chevron-right" /></span>
        </div>
      </div>
    </button>
  );
}

function Tips({ items }: { items: string[] }) {
  return (
    <>
      <div className="section-label"><i className="fa fa-lightbulb-o" style={{ color: "var(--warning)" }} /> 使用说明</div>
      <ul className="tips-list">
        {items.map((item) => <li key={item}><i className="fa fa-check-circle" /><span>{item}</span></li>)}
      </ul>
    </>
  );
}

function ImageResult({ result }: { result: ImageDetectionResult }) {
  const probability = Math.round((result.probability || 0) * 1000) / 10;
  return (
    <div className="result-panel">
      <div className="section-label"><i className="fa fa-bar-chart" /> 检测结果</div>
      {result.image_url && <img className="result-media" src={result.image_url} alt={result.filename} />}
      <div className="verdict-row">
        <span className={result.final_label.includes("AI") ? "pill danger" : "pill ok"}>{result.final_label}</span>
        <strong>{probability}%</strong>
      </div>
      <div className="case-kv">
        <Info label="置信度" value={result.confidence || "-"} />
        <Info label="文件名" value={result.filename || "-"} />
        <Info label="格式" value={result.img_format || "-"} />
        <Info label="分辨率" value={result.resolution || "-"} />
      </div>
      <div className="result-actions">
        <button className="btn-code" type="button" onClick={() => downloadImageReport(result.itemid)}>
          <i className="fa fa-download" /> 下载报告
        </button>
      </div>
      <div className="case-block"><p>{result.explanation}</p></div>
    </div>
  );
}

function VideoResult({ result }: { result: VideoDetectionResult }) {
  return (
    <div className="result-panel">
      <div className="section-label"><i className="fa fa-bar-chart" /> 视频检测结果</div>
      {result.video_url && <video className="result-media" src={result.video_url} controls />}
      <div className="verdict-row">
        <span className={result.final_label.includes("AI") ? "pill danger" : "pill ok"}>{result.final_label || "未标注"}</span>
        <strong>{Math.round(result.fake_percentage * 10) / 10}%</strong>
      </div>
      <Progress label="真实概率" value={result.real_percentage} tone="green" />
      <Progress label="AI概率" value={result.fake_percentage} tone="red" />
      <div className="result-actions">
        <button className="btn-code" type="button" onClick={() => downloadVideoReport(result.itemid)}>
          <i className="fa fa-download" /> 下载报告
        </button>
      </div>
      <div className="case-block"><p>{result.explanation || "暂无详细说明"}</p></div>
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

function RetrieveResults({ results, baseUrl, searchType }: { results: RetrieveItem[]; baseUrl: string; searchType: "image" | "video" }) {
  return (
    <div className="results-section visible">
      <div className="card result-card-wrap">
        <div className="results-header">
          <h2><i className="fa fa-bar-chart" /> 侵权检索结果</h2>
          <div className="results-summary">共 {results.length} 条可疑结果</div>
        </div>
        <div className="results-grid">
          {results.slice(0, 15).map((item) => {
            const mediaPath = item.product?.product_images || item.id;
            const src = `${baseUrl}${encodeURI(mediaPath)}`;
            return (
              <div className="result-card" key={`${item.rank}-${item.id}`}>
                <div className="result-thumb">
                  {searchType === "image" ? <img src={src} alt={`第 ${item.rank} 名`} /> : <video src={src} />}
                  <div className={`rank-badge ${item.rank <= 3 ? `rank-${item.rank}` : "rank-default"}`}>{item.rank}</div>
                </div>
                <div className="result-body">
                  <div className="result-name">{item.id}</div>
                  <div className="result-score-row"><span className="result-score-label">相似度</span><span className="result-score-val">{Number(item.score || 0).toFixed(3)}</span></div>
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
}: {
  records: HistoryRecord[];
  tab: "image" | "video" | "imageRetrieve" | "videoRetrieve";
  query: string;
}) {
  const isVideo = tab === "video" || tab === "videoRetrieve";
  const isRetrieval = tab === "imageRetrieve" || tab === "videoRetrieve";
  return (
    <div className="history-grid">
      {records.map((record, index) => {
        const mediaUrl = historyMediaUrl(record);
        const previewUrl = historyPreviewUrl(record) || mediaUrl;
        const title = String(record.filename || `历史记录 ${index + 1}`);
        const resultCount = Number(record.result_count || 0);
        const verdict = isRetrieval
          ? `${resultCount} 条结果`
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
        const retrievalTag = tab === "imageRetrieve" ? "图像检索" : tab === "videoRetrieve" ? "视频检索" : "";
        const hasHits = resultCount > 0;
        const topResultId = String(record.top_result_id || "");
        const topResultLibrary = String(record.top_result_library || "");
        const topResultScore = Number(record.top_result_score || 0);
        return (
          <article className="history-record" key={`${record.itemid || index}`}>
            <a className="history-media" href={mediaUrl || undefined} target={mediaUrl ? "_blank" : undefined} rel="noreferrer" aria-label={mediaUrl ? `查看 ${title}` : title}>
              {previewUrl ? (
                isVideo ? (
                  <div className="history-placeholder"><i className="fa fa-film" /></div>
                ) : (
                  <img src={previewUrl} alt={title} loading="lazy" />
                )
              ) : (
                <div className="history-placeholder"><i className={`fa ${isVideo ? "fa-film" : "fa-image"}`} /></div>
              )}
              {mediaUrl && <span className="history-view"><i className="fa fa-eye" /> 查看</span>}
            </a>
            <div className="history-body">
              <div className="history-title" title={title}>{renderHighlightedText(title, query)}</div>
              {guestRecord && (
                <div className="history-tags">
                  <span className="history-tag guest"><i className="fa fa-user-secret" /> {renderHighlightedText("访客", query)}</span>
                  {hasMetadata && <span className="history-tag meta"><i className="fa fa-info-circle" /> {renderHighlightedText("元数据", query)}</span>}
                  {hasIssues && <span className="history-tag issue"><i className="fa fa-exclamation-triangle" /> {renderHighlightedText(`可疑点${issueCount > 0 ? ` ${issueCount}` : ""}`, query)}</span>}
                </div>
              )}
              {!guestRecord && (hasMetadata || hasIssues) && (
                <div className="history-tags">
                  {hasMetadata && <span className="history-tag meta"><i className="fa fa-info-circle" /> {renderHighlightedText("元数据", query)}</span>}
                  {hasIssues && <span className="history-tag issue"><i className="fa fa-exclamation-triangle" /> {renderHighlightedText(`可疑点${issueCount > 0 ? ` ${issueCount}` : ""}`, query)}</span>}
                </div>
              )}
              {isRetrieval ? (
                <div className="history-tags">
                  {retrievalTag && <span className="history-tag meta"><i className="fa fa-search" /> {renderHighlightedText(retrievalTag, query)}</span>}
                  <span className={`history-tag ${hasHits ? "meta" : "issue"}`}>
                    <i className={`fa ${hasHits ? "fa-check-circle" : "fa-minus-circle"}`} />
                    {renderHighlightedText(hasHits ? "有命中" : "无命中", query)}
                  </span>
                </div>
              ) : null}
              <div className="history-row"><span>时间</span><strong>{renderHighlightedText(timeText, query)}</strong></div>
              <div className="history-row"><span>{isRetrieval ? "数量" : "结论"}</span><strong>{renderHighlightedText(verdict, query)}</strong></div>
              <div className="history-row"><span>{isRetrieval ? "Top-K" : "置信度"}</span><strong>{renderHighlightedText(meta, query)}</strong></div>
              {isRetrieval && topResultLibrary && (
                <div className="history-row"><span>命中库</span><strong>{renderHighlightedText(topResultLibrary, query)}</strong></div>
              )}
              {isRetrieval && topResultId && (
                <>
                  <div className="history-row"><span>首个命中</span><strong>{renderHighlightedText(topResultId, query)}</strong></div>
                  <div className="history-row"><span>最高分</span><strong>{renderHighlightedText(topResultScore.toFixed(4), query)}</strong></div>
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
                    <i className="fa fa-download" /> 报告
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
                    <i className="fa fa-download" /> 报告
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

function AuthModal({ onAuthed, onClose }: { onAuthed: () => Promise<void>; onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="login-card modal-login-card">
        <button className="case-modal-close modal-close" onClick={onClose}><i className="fa fa-times" /></button>
        <div className="login-header">
          <span className="login-icon"><i className="fa fa-shield" /></span>
          <div>
            <h2>账户登录</h2>
            <p className="sub">登录后 30 天内自动保持状态</p>
          </div>
        </div>
        <AuthForm onAuthed={onAuthed} />
      </div>
    </div>
  );
}

function AuthForm({ onAuthed }: { onAuthed: () => Promise<void> }) {
  const [mode, setMode] = useState<AuthMode>("password");
  const [phone, setPhone] = useState("");
  const [secret, setSecret] = useState("");
  const [username, setUsername] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [status, setStatus] = useState<Status>(null);
  const [busy, setBusy] = useState(false);
  const [codeBusy, setCodeBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  async function sendCode(scene: "login" | "register") {
    setStatus(null);
    if (!/^1[3-9]\d{9}$/.test(phone.trim())) {
      setStatus({ tone: "error", text: "请输入正确的手机号" });
      return;
    }
    setCodeBusy(true);
    try {
      const data = await sendSmsCode(phone, scene);
      if (data.debug_code) {
        setSmsCode(data.debug_code);
        setStatus({ tone: "ok", text: `开发模式已自动填入验证码：${data.debug_code}` });
      } else {
        setStatus({ tone: "ok", text: "验证码已发送，请查看短信" });
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
      setStatus({ tone: "error", text: "请输入正确的手机号" });
      return;
    }
    if ((mode === "sms" || mode === "register") && !smsCode.trim()) {
      setStatus({ tone: "error", text: "请输入短信验证码" });
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      if (mode === "password") await loginByPassword(phone, secret);
      else if (mode === "sms") await loginBySms(phone, smsCode);
      else {
        if (!secret.trim()) {
          setStatus({ tone: "error", text: "请设置登录密码" });
          return;
        }
        await registerUser({ phone, secret, username, sms_code: smsCode });
        setStatus({ tone: "ok", text: "注册成功，请切换到登录" });
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
        <button type="button" className={`login-tab ${mode === "password" ? "active" : ""}`} onClick={() => setMode("password")}>密码登录</button>
        <button type="button" className={`login-tab ${mode === "sms" ? "active" : ""}`} onClick={() => setMode("sms")}>验证码登录</button>
        <button type="button" className={`login-tab ${mode === "register" ? "active" : ""}`} onClick={() => setMode("register")}>注册</button>
      </div>
      <form onSubmit={submit} className="login-panel active">
        <AuthInput icon="fa-phone" label="手机号" value={phone} onChange={setPhone} placeholder="请输入手机号" />
        {mode === "register" && <AuthInput icon="fa-user" label="用户名" value={username} onChange={setUsername} placeholder="请输入用户名" />}
        {(mode === "password" || mode === "register") && <AuthInput icon="fa-lock" label="密码" value={secret} onChange={setSecret} placeholder="请输入密码" type="password" />}
        {(mode === "sms" || mode === "register") && (
          <div className="form-group">
            <label className="form-label">短信验证码</label>
            <div className="code-row">
              <div className="input-wrap">
                <i className="fa fa-shield" />
                <input value={smsCode} onChange={(event) => setSmsCode(event.target.value)} placeholder="请输入验证码" />
              </div>
              <button
                type="button"
                className="btn-code"
                disabled={codeBusy || cooldown > 0}
                onClick={() => sendCode(mode === "register" ? "register" : "login")}
              >
                {codeBusy ? "发送中" : cooldown > 0 ? `${cooldown}s` : "获取验证码"}
              </button>
            </div>
          </div>
        )}
        {status && <div className={`notice ${status.tone}`}>{status.text}</div>}
        <button type="submit" className="btn-primary" disabled={busy}><i className="fa fa-sign-in" /> {mode === "register" ? "创建账号" : "登录"}</button>
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

function Footer() {
  return (
    <footer className="footer">
      <div className="footer-logo"><i className="fa fa-eye" /> 数字内容鉴伪平台</div>
      <p className="footer-copy">&copy; 2025 数字内容鉴伪平台</p>
    </footer>
  );
}

function formatSize(size: number) {
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

function colorBg(color: string) {
  if (color === "var(--primary)") return "rgba(37,99,235,0.1)";
  if (color === "var(--warning)") return "rgba(245,158,11,0.1)";
  if (color === "var(--accent)") return "rgba(6,214,160,0.1)";
  return "rgba(139,92,246,0.1)";
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

function getHistoryFilterOptions(tab: HistoryTabKey): Array<{ key: HistoryFilterKey; label: string }> {
  if (tab === "image") {
    return [
      { key: "all", label: "全部" },
      { key: "guest", label: "访客" },
      { key: "metadata", label: "元数据" },
      { key: "issues", label: "可疑点" },
    ];
  }
  if (tab === "video") {
    return [
      { key: "all", label: "全部" },
      { key: "guest", label: "访客" },
      { key: "ai", label: "AI结论" },
      { key: "real", label: "真实结论" },
    ];
  }
  return [
    { key: "all", label: "全部" },
    { key: "hits", label: "有命中" },
    { key: "empty", label: "无命中" },
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

function getHistoryActiveSummary(tab: HistoryTabKey, filter: HistoryFilterKey, query: string) {
  const tabLabels: Record<HistoryTabKey, string> = {
    image: "图像鉴伪",
    video: "视频鉴伪",
    imageRetrieve: "图像检索",
    videoRetrieve: "视频检索",
  };
  const filterLabel =
    getHistoryFilterOptions(tab).find((option) => option.key === filter)?.label || "全部";
  return [
    { label: "模块", value: tabLabels[tab] },
    { label: "筛选", value: filterLabel },
    { label: "搜索", value: query.trim() || "未设置" },
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
