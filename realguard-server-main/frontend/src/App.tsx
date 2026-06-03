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
const IMAGE_MAX_BYTES = 25 * 1024 * 1024;
const VIDEO_MAX_BYTES = 512 * 1024 * 1024;
const V2_CONSOLE_MAX_BYTES = 25 * 1024 * 1024;

function formatUsageNumber(value: number | undefined | null) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatUsageDate(value: string | undefined | null) {
  if (!value) return "暂无调用";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

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
      {page === "developer" && <DeveloperPlatformPage user={user} onNeedAuth={requireAuth} />}

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
  const totalDetect = counters.image_detect + counters.video_detect || 10000;
  const totalRetrieve = counters.image_retrieve + counters.video_retrieve || 5000000;
  const workflowCards = [
    {
      step: "01",
      title: "内容审核团队",
      desc: "上传图像或视频，直接获得真伪结论、置信度、证据字段和报告编号。",
      action: "开始鉴伪",
      icon: "fa-shield",
      onClick: () => setPage("image"),
    },
    {
      step: "02",
      title: "版权检索场景",
      desc: "对疑似侵权素材做相似内容检索，把重复传播和素材来源拉到同一视图。",
      action: "检索相似内容",
      icon: "fa-crosshairs",
      onClick: () => setPage("retrieve"),
    },
    {
      step: "03",
      title: "外部 Agent",
      desc: "复制公开 Skill，让 OpenClaw 或其他 agent 使用 V2/V1 API 完成鉴伪。",
      action: "复制 Skill",
      icon: "fa-plug",
      onClick: () => setPage("developer"),
    },
    {
      step: "04",
      title: "开发者接入",
      desc: "生成个人 API Key，追踪调用次数与 Token 成本，并在线调试返回 JSON。",
      action: "打开文档",
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
              <span>REALGUARD INTELLIGENCE</span>
              <i>Agent-ready forensic platform</i>
            </div>
            <h1>
              <span>让 AIGC 鉴伪</span>
              <span>进入证据链</span>
            </h1>
            <p>
              RealGuard 面向内容审核、版权检索和外部 AI Agent，把图像/视频检测、取证证据、报告 ID、
              API Key 与用量统计组织成一条清晰的业务流程，而不是把用户丢进零散功能入口。
            </p>
            <div className="home-hero-actions">
              <button className="home-primary-action" onClick={() => setPage("image")}>
                开始检测 <i className="fa fa-arrow-right" />
              </button>
              <button className="home-secondary-action" onClick={() => setPage("developer")}>
                接入开发者平台 <i className="fa fa-code" />
              </button>
            </div>
            <div className="home-trust-row" aria-label="平台能力摘要">
              <div>
                <strong>V1 / V2</strong>
                <span>双链路鉴伪</span>
              </div>
              <div>
                <strong>{totalDetect.toLocaleString("zh-CN")}+</strong>
                <span>检测任务</span>
              </div>
              <div>
                <strong>{totalRetrieve.toLocaleString("zh-CN")}+</strong>
                <span>检索任务</span>
              </div>
            </div>
          </div>

          <div className="home-briefing-board fade-up visible" aria-label="RealGuard 证据简报">
            <div className="briefing-label">
              <span>Live Evidence Brief</span>
              <b>RG-0427</b>
            </div>
            <div className="briefing-image-card primary">
              <img src="/system/case2.webp" alt="AI 生成检测示例" />
              <div>
                <span>综合判断</span>
                <strong>AI 生成风险 73.9%</strong>
              </div>
            </div>
            <div className="briefing-image-card secondary">
              <img src="/system/case1.webp" alt="泳池场景检测示例" />
              <div>
                <span>辅助证据</span>
                <strong>纹理与边缘异常</strong>
              </div>
            </div>
            <div className="briefing-feed">
              <div><span>ELA</span><strong>压缩误差图</strong><small>异常区域定位</small></div>
              <div><span>Noise</span><strong>噪声残差</strong><small>生成纹理比对</small></div>
              <div><span>Usage</span><strong>Calls + Tokens</strong><small>开发者成本审计</small></div>
            </div>
            <div className="briefing-agent-card">
              <span>Agent handoff</span>
              <code>Use $realguard-forensics · POST /v2-api/detect</code>
            </div>
          </div>
        </div>
      </section>

      <section className="section home-workflow-section">
        <div className="container">
          <div className="home-section-heading">
            <span>Start from task</span>
            <h2>四条路径，直接进入你要完成的工作。</h2>
            <p>审核人员从检测开始，版权团队从检索开始，外部 Agent 从 Skill 开始，开发者从 API Key 和文档开始。</p>
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
          <SkillEntryPanel />
        </div>
      </section>

      <section className="section section-alt home-capability-section">
        <div className="container">
          <SectionHeader title="核心能力" desc="检测、检索、报告与开发者接入保持独立，但在首页以同一条证据链呈现。" />
          <div className="features-grid">
            <FeatureCard accent="var(--primary)" icon="fa-image" title="图像鉴伪" desc="基于深度学习的图像真伪识别，支持多种场景的AIGC图像检测" onClick={() => setPage("image")} />
            <FeatureCard accent="var(--warning)" icon="fa-film" title="视频鉴伪" desc="针对视频内容的 AI 生成检测与篡改识别，帧级分析定位可疑片段。" onClick={() => setPage("video")} />
            <FeatureCard accent="var(--accent)" icon="fa-search" title="图像侵权检索" desc="检索疑似侵权的图像，在图像数据库中快速定位可疑图像内容。" onClick={() => setPage("retrieve")} />
            <FeatureCard accent="var(--primary-light)" icon="fa-play-circle" title="视频侵权检索" desc="检索疑似侵权的视频，在数据库中快速定位相似可疑视频内容。" onClick={() => setPage("retrieve")} />
            <FeatureCard accent="var(--primary-dark)" icon="fa-bolt" title="V2 鉴伪 Agent" desc="独立新版系统，使用 qwen3-vl-flash 融合 ELA、噪声残差等取证证据。" onClick={() => { window.location.href = "/v2/"; }} />
          </div>
        </div>
      </section>

      <section className="section section-default">
        <div className="container">
          <SectionHeader title="结果不是一句话，而是一组可复核证据" desc="示例卡保留判断比例，但首页更强调报告、证据字段和后续追踪。" />
          <div className="examples-grid">
            <ExampleCard image="/system/case1.webp" title="案例一：泳池场景人物图像" desc="综合判断为 AI 生成（53.8%），点击查看检测结果。" real={46.2} fake={53.8} />
            <ExampleCard image="/system/case2.webp" title="案例二：几何色块人像图像" desc="综合判断为 AI 生成（73.9%），点击查看检测结果。" real={26.1} fake={73.9} />
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

function CopySnippetCard({
  id,
  title,
  desc,
  text,
  copiedId,
  onCopy,
  variant = "default",
}: {
  id: string;
  title: string;
  desc: string;
  text: string;
  copiedId: string;
  onCopy: (id: string, text: string) => void;
  variant?: "default" | "primary" | "compact";
}) {
  const copied = copiedId === id;
  return (
    <article className={`copy-snippet-card copy-snippet-card-${variant} ${copied ? "copied" : ""}`}>
      <div className="copy-snippet-head">
        <span className="copy-snippet-status">{copied ? "已复制" : "Ready to copy"}</span>
        <button type="button" onClick={() => onCopy(id, text)} aria-label={`复制 ${title}`}>
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <strong>{title}</strong>
      <p>{desc}</p>
      <pre><code>{text}</code></pre>
    </article>
  );
}

async function copyTextToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  window.prompt("复制以下内容", text);
}

function SkillEntryPanel() {
  const [copiedId, setCopiedId] = useState("");

  async function copySkill(id: string, text: string) {
    await copyTextToClipboard(text);
    setCopiedId(id);
    window.setTimeout(() => setCopiedId(""), 1800);
  }

  return (
    <div className="skill-entry-panel fade-up visible">
      <div className="skill-entry-main">
        <div className="skill-entry-badges">
          <span><i className="fa fa-plug" /> SKILL 已介入</span>
          <span>OpenClaw / AI Agent 可调用</span>
        </div>
        <h3>把 RealGuard 变成外部 Agent 可直接调用的鉴伪工具</h3>
        <p>
          外部 agent 不需要知道你的服务器目录，也不需要猜接口字段。复制下面的交接语后，它会先读取公网 Skill，
          再按 V2 多模态或 V1 图像模型输出可审计结论。
        </p>
        <details className="skill-reason">
          <summary>为什么必须公开 Skill</summary>
          <p>
            OpenClaw 等外部 agent 访问不到本地仓库路径，也不知道报告字段、解释边界和鉴伪输出规范。
            公开 <code>{REALGUARD_SKILL_URL}</code> 后，任何 agent 都能用同一份说明完成调用，并把调用次数与 Token 用量纳入审计。
          </p>
        </details>
        <div className="skill-protocol-rail" aria-label="Skill 接入流程">
          <div>
            <span>01 PUBLIC</span>
            <strong>读取公网 Skill</strong>
            <small>不依赖本地路径，外部 Agent 可访问</small>
          </div>
          <div>
            <span>02 V2 / V1</span>
            <strong>选择模型链路</strong>
            <small>V2 默认，V1 兼容旧图像模型</small>
          </div>
          <div>
            <span>03 AUDIT</span>
            <strong>返回证据与用量</strong>
            <small>结论、报告、调用次数、Token 统计</small>
          </div>
        </div>
      </div>
      <div className="skill-entry-code">
        <div className="skill-terminal-label">
          <span>Recommended handoff</span>
          <strong>V2 first · click copy</strong>
        </div>
        <CopySnippetCard id="skill-v2" title="复制 V2 Skill 调用" desc="优先推荐：多模态检测、报告、tokenUsage 和模型版本更完整。" text={REALGUARD_SKILL_HANDOFF_V2} copiedId={copiedId} onCopy={copySkill} variant="primary" />
        <div className="skill-secondary-copy-grid">
          <CopySnippetCard id="skill-url" title="公开 Skill URL" desc="给别的 agent 的第一步：先读取公共说明。" text={REALGUARD_SKILL_URL} copiedId={copiedId} onCopy={copySkill} variant="compact" />
          <CopySnippetCard id="skill-v1" title="复制 V1 Skill 调用" desc="兼容 RealGuard V1 图像模型，并统计调用次数。" text={REALGUARD_SKILL_HANDOFF_V1} copiedId={copiedId} onCopy={copySkill} variant="compact" />
        </div>
        <div className="skill-cta-row">
          <button onClick={() => { window.location.href = "/v2/"; }}>
            进入 V2 Agent <i className="fa fa-arrow-right" />
          </button>
          <button onClick={() => { window.location.href = "/?page=developer"; }}>
            打开开发者平台 <i className="fa fa-code" />
          </button>
        </div>
      </div>
    </div>
  );
}

function DeveloperPlatformPage({ user, onNeedAuth }: { user: User | null; onNeedAuth: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [keys, setKeys] = useState<DeveloperApiKey[]>([]);
  const [keyName, setKeyName] = useState("默认生产 Key");
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
    setKeyStatus({ tone: "info", text: "正在生成 API Key..." });
    try {
      const data = await createDeveloperApiKey(keyName);
      setGeneratedKey(data.apiKey);
      setApiKey(data.apiKey);
      setKeys((current) => [data.key, ...current.filter((item) => item.id !== data.key.id)]);
      setKeyStatus({ tone: "ok", text: "API Key 已生成。完整 key 只显示一次，请立即复制保存。" });
    } catch (error) {
      setKeyStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setKeyBusy(false);
    }
  }

  async function handleRevokeKey(keyId: number) {
    setKeyBusy(true);
    setKeyStatus({ tone: "info", text: "正在撤销 API Key..." });
    try {
      await revokeDeveloperApiKey(keyId);
      await loadDeveloperKeys();
      setKeyStatus({ tone: "ok", text: "API Key 已撤销，后续请求会被拒绝。" });
    } catch (error) {
      setKeyStatus({ tone: "error", text: errorMessage(error) });
    } finally {
      setKeyBusy(false);
    }
  }

  async function copyDeveloperText(id: string, text: string) {
    await copyTextToClipboard(text);
    setCopiedDocId(id);
    window.setTimeout(() => setCopiedDocId(""), 1800);
  }
  const docsNavGroups = [
    {
      title: "开始使用",
      links: [
        ["#overview", "总览"],
        ["#quickstart", "快速开始"],
        ["#skill-copy", "复制 Skill"],
        ["#auth", "认证"],
        ["#api-keys", "API Keys"],
        ["#token-usage", "调用统计"],
      ],
    },
    {
      title: "API Reference",
      links: [
        ["#reference", "接口总览"],
        ["#detect", "Detect"],
        ["#v1-detect", "V1 Detect"],
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
    { method: "GET", path: "/health", title: "Health", desc: "公开服务状态、能力摘要、上传限制和访问保护状态。", anchor: "#health" },
    { method: "GET", path: "/admin/health", title: "Admin Health", desc: "受保护的详细诊断接口，返回模型、校准、存储等内部状态。", anchor: "#admin-health" },
    { method: "POST", path: "/detect", title: "V2 Detect", desc: "V2 多模态鉴伪接口。multipart 上传 file，可选 fileType。", anchor: "#detect" },
    { method: "POST", path: "/api/developer/v1/detect", title: "V1 Detect", desc: "V1 图像模型接口。multipart 上传 file，记录调用次数。", anchor: "#v1-detect" },
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
    ["tokenUsage", "本次模型调用的 prompt / completion / total token；缓存命中时为 0。"],
    ["source", "vlm / mock / heuristic 等，决定结果可信度说明。"],
    ["reportId", "可用于下载和归档报告的编号。"],
    ["synthid / visibleWatermark", "水印、SynthID、可见水印等附加证据。"],
  ];
  const v1Fields = [
    ["result.final_label", "V1 图像模型输出的最终标签，例如 AI生成图像 / 真实图像。"],
    ["result.probability", "V1 置信概率，用于排序和阈值判断。"],
    ["result.confidence", "V1 置信等级。"],
    ["result.visual_issues", "图像可疑区域、视觉问题或辅助证据。"],
    ["result.itemid", "V1 站内报告和历史记录使用的编号。"],
  ];
  const errorRows = [
    ["400", "Bad Request", "缺少 file、fileType 不合法或 multipart 格式错误。"],
    ["401", "Unauthorized", "需要 API Key 的接口未传 Key、Key 无效或已撤销。"],
    ["403", "Forbidden", "API Key 无权访问该报告或资源。"],
    ["413", "Payload Too Large", "文件超过服务允许大小，需要压缩或走异步/分片流程。"],
    ["422", "Unprocessable Entity", "文件格式无法识别或不支持当前检测链路。"],
    ["500", "Internal Server Error", "服务端分析失败；记录 taskId 并重试或转人工处理。"],
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
    setConsoleStatus({ tone: "info", text: "正在检查 V2 API 状态..." });
    try {
      const result = await getV2Health(apiKey);
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
    const message = validateFile(testFile, { kind: "测试文件", maxBytes: V2_CONSOLE_MAX_BYTES });
    if (message) {
      setConsoleStatus({ tone: "error", text: message });
      return;
    }
    const started = performance.now();
    setConsoleBusy(true);
    setConsoleStatus({ tone: "info", text: "正在上传文件并调用鉴伪 API..." });
    try {
      const result = await runV2Detect({ file: testFile, fileType, token: apiKey });
      setConsoleResult(result as Record<string, unknown>);
      setConsoleMeta({ endpoint: "POST /detect", elapsedMs: Math.round(performance.now() - started), at: new Date().toLocaleString() });
      setConsoleStatus({ tone: "ok", text: `检测完成：${result.verdict || "已返回结果"}` });
      void loadDeveloperUsage(usageDays);
    } catch (error) {
      setConsoleStatus({ tone: "error", text: error instanceof Error ? error.message : "检测失败" });
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
      title: "V2 多模态 Skill 调用",
      desc: "推荐默认使用：返回 agentSummary、报告 ID、模型版本和 tokenUsage。",
      endpoint: "POST /v2-api/detect",
      text: REALGUARD_SKILL_HANDOFF_V2,
    },
    {
      id: "v1" as DeveloperSkillMode,
      title: "V1 图像模型 Skill 调用",
      desc: "用于兼容旧图像鉴伪链路，统计调用次数，响应使用 result.* 字段。",
      endpoint: "POST /api/developer/v1/detect",
      text: REALGUARD_SKILL_HANDOFF_V1,
    },
  ];
  const activeSkill = skillOptions.find((item) => item.id === skillMode) || skillOptions[0];
  const developerActionCards = [
    { href: "#api-keys", icon: "fa-key", title: "生成 API Key", desc: "注册登录后生成 rg_sk_，绑定个人账号并可随时撤销。" },
    { href: "#skill-copy", icon: "fa-copy", title: "复制 Skills 接入", desc: "一键复制 V2 或 V1 handoff，让外部 agent 直接使用。" },
    { href: "#token-usage", icon: "fa-line-chart", title: "查看调用统计", desc: "同时统计 V1/V2 调用次数、Token、缓存命中和端点分布。" },
    { href: "#console", icon: "fa-terminal", title: "在线测试 API", desc: "直接粘贴 Key、上传文件并查看返回 JSON。" },
  ];

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
                  <span>Public API</span>
                  <span>Agent Skill</span>
                  <span>V1 / V2</span>
                  <span>Multipart Upload</span>
                </div>
                <h1>RealGuard Developer Platform</h1>
                <p>
                  面向 OpenClaw、外部 AI Agent 和业务系统的统一接入台。
                  这里把 API Key、公开 Skill、V1/V2 调用、用量统计和在线测试放在同一屏，避免用户在长文档里找入口。
                </p>
                <div className="developer-command-strip" aria-label="开发者接入流程">
                  <span><b>01</b> Issue Key</span>
                  <span><b>02</b> Copy Skill</span>
                  <span><b>03</b> Run Detect</span>
                  <span><b>04</b> Audit Usage</span>
                </div>
                <div className="docs-hero-actions">
                  <a href="#api-keys">生成 API Key</a>
                  <a href="#skill-copy" className="secondary">复制 Skill 调用</a>
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
                    <span>Skills Copy</span>
                    <strong>点击即可复制给外部 Agent</strong>
                  </div>
                  <code>{activeSkill.endpoint}</code>
                </div>
                <div className="skill-mode-toggle" role="tablist" aria-label="选择 Skill 调用模式">
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
                  variant="primary"
                />
              </div>
            </section>

            <nav className="developer-workflow-strip" aria-label="开发者接入工作流">
              {developerActionCards.map((item) => (
                <a href={item.href} key={item.href}>
                  <i className={`fa ${item.icon}`} />
                  <strong>{item.title}</strong>
                  <small>{item.desc}</small>
                </a>
              ))}
            </nav>

            <section id="quickstart" className="docs-section">
              <div className="docs-section-kicker">Getting Started</div>
              <h2>快速开始</h2>
              <p className="docs-lead">
                第一次接入只需要三步：读取公开 Skill、按场景选择 V2 多模态或 V1 图像模型、按关键字段输出可审计结论。
                V2 读取 <code>agentSummary</code> / <code>tokenUsage</code>，V1 读取 <code>result.final_label</code> / <code>result.itemid</code>。
              </p>
              <div className="docs-callout docs-callout-strong">
                <h3>为什么必须公开 Skill</h3>
                <p>
                  别的 agent 访问不到你的本地路径，也无法猜测接口字段、报告下载地址和解释边界。
                  公开 Skill 后，OpenClaw 只要读取公网 URL，就能稳定调用 API，并知道哪些字段必须带入最终鉴伪结论。
                </p>
                <div className="docs-copy-grid">
                  <CopySnippetCard id="docs-skill-url" title="复制 Skill URL" desc="给外部 agent 的第一步：读取公共说明。" text={REALGUARD_SKILL_URL} copiedId={copiedDocId} onCopy={copyDeveloperText} variant="compact" />
                  <CopySnippetCard id="docs-skill-v2" title="复制 V2 Handoff" desc="默认推荐，多模态结果和 tokenUsage 更完整。" text={REALGUARD_SKILL_HANDOFF_V2} copiedId={copiedDocId} onCopy={copyDeveloperText} variant="primary" />
                  <CopySnippetCard id="docs-skill-v1" title="复制 V1 Handoff" desc="兼容 V1 图像模型，纳入调用次数统计。" text={REALGUARD_SKILL_HANDOFF_V1} copiedId={copiedDocId} onCopy={copyDeveloperText} variant="compact" />
                </div>
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
                调用 <code>/detect</code>、<code>/forensics</code>、<code>/provenance</code> 和报告下载接口时，
                请使用开发者平台生成的个人 API Key。每个 Key 绑定到登录用户，可撤销、可审计。
              </p>
              <div className="docs-code-block compact">
                <pre>{`X-RealGuard-Key: rg_sk_xxx
Authorization: Bearer rg_sk_xxx`}</pre>
              </div>
              <div className="docs-callout">
                <strong>安全建议</strong>
                <p>API Key 不要写进前端源码或公开仓库。自动化 agent 应使用独立 Key，并记录调用人、时间、文件摘要和报告 ID。</p>
              </div>
              <div className="docs-callout">
                <strong>运维 Token</strong>
                <p>
                  <code>X-Jianzhen-Token</code> 仅用于 <code>/admin/health</code>、<code>/history</code>、
                  <code>/metrics</code> 等管理接口，不应发给普通开发者或外部 agent。
                </p>
              </div>
            </section>

            <section id="api-keys" className="docs-section auth-manager-section">
              <div className="docs-section-kicker">API Key Management</div>
              <h2>我的 API Key</h2>
              <p className="docs-lead">
                注册并登录开发者平台后，可以生成自己的 <code>rg_sk_</code> Key。完整 Key 只在创建时显示一次；
                列表中只保留预览、状态和最后使用时间。
              </p>
              {!user ? (
                <div className="docs-callout docs-callout-strong">
                  <h3>需要先注册/登录</h3>
                  <p>API Key 需要绑定到真实账号，用于调用审计、撤销和报告权限控制。</p>
                  <button className="docs-inline-button" onClick={onNeedAuth}>注册/登录开发者平台</button>
                </div>
              ) : (
                <>
                  <div className="api-key-manager">
                    <div className="api-key-create">
                      <div>
                        <span>当前账号</span>
                        <strong>{user.username || user.phone}</strong>
                        <small>{user.phone}</small>
                      </div>
                      <label>
                        Key 名称
                        <input value={keyName} maxLength={120} onChange={(event) => setKeyName(event.target.value)} />
                      </label>
                      <button disabled={keyBusy} onClick={handleCreateKey}>
                        <i className={`fa ${keyBusy ? "fa-spinner detect-spin" : "fa-key"}`} /> 生成 API Key
                      </button>
                      {keyStatus && <StatusPill status={keyStatus} />}
                      {generatedKey && (
                        <div className="generated-key-box">
                          <span>完整 Key 只显示一次</span>
                          <code>{generatedKey}</code>
                          <div>
                            <button onClick={() => copyDeveloperText("generated-key", generatedKey)}>
                              {copiedDocId === "generated-key" ? "已复制" : "复制 Key"}
                            </button>
                            <button onClick={() => setApiKey(generatedKey)}>填入测试台</button>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="api-key-list">
                      <div className="api-key-list-head">
                        <strong>已创建 Key</strong>
                        <button disabled={keyBusy} onClick={loadDeveloperKeys}>刷新</button>
                      </div>
                      {keys.length === 0 ? (
                        <p className="empty-key-state">暂无 API Key。生成后即可在外部 agent 或业务系统中调用接口。</p>
                      ) : (
                        keys.map((item) => (
                          <div className={`api-key-row ${item.status === "active" ? "active" : "revoked"}`} key={item.id}>
                            <div>
                              <strong>{item.name}</strong>
                              <code>{item.preview}</code>
                              <span>
                                创建：{item.createdAt || "-"} · 最后使用：{item.lastUsedAt || "未使用"}
                              </span>
                            </div>
                            <div>
                              <small>{item.status}</small>
                              {item.status === "active" && (
                                <button disabled={keyBusy} onClick={() => handleRevokeKey(item.id)}>撤销</button>
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
                        <h3>调用次数与 Token 统计</h3>
                        <p>
                          统计不只看 Token，也要看调用次数。V1 图像模型和 V2 多模态接口会合并展示，便于发现外部 agent 重试、
                          Key 滥用、缓存命中和真实模型成本变化。
                        </p>
                      </div>
                      <div className="token-usage-actions">
                        <select value={usageDays} onChange={(event) => setUsageDays(Number(event.target.value))}>
                          <option value={7}>近 7 天</option>
                          <option value={14}>近 14 天</option>
                          <option value={30}>近 30 天</option>
                          <option value={90}>近 90 天</option>
                        </select>
                        <button disabled={usageBusy} onClick={() => loadDeveloperUsage(usageDays)}>
                          <i className={`fa ${usageBusy ? "fa-spinner detect-spin" : "fa-refresh"}`} /> 刷新用量
                        </button>
                      </div>
                    </div>
                    {usageStatus && <StatusPill status={usageStatus} />}
                    <div className="token-usage-metrics">
                      <div className="token-usage-metric primary">
                        <span>总调用次数</span>
                        <strong>{formatUsageNumber(totalCalls)}</strong>
                        <small>
                          V1 {formatUsageNumber(v1Calls)} 次 / V2 {formatUsageNumber(v2Calls)} 次
                        </small>
                      </div>
                      <div className="token-usage-metric">
                        <span>Token 总量</span>
                        <strong>{formatUsageNumber(usageSummary?.totalTokens)}</strong>
                        <small>
                          Prompt {formatUsageNumber(usageSummary?.promptTokens)} / Completion {formatUsageNumber(usageSummary?.completionTokens)}
                        </small>
                      </div>
                      <div className="token-usage-metric">
                        <span>V1 图像模型</span>
                        <strong>{formatUsageNumber(v1Calls)}</strong>
                        <small>V1 记录调用次数；响应不返回 tokenUsage</small>
                      </div>
                      <div className="token-usage-metric">
                        <span>V2 多模态</span>
                        <strong>{formatUsageNumber(v2Calls)}</strong>
                        <small>缓存命中 {formatUsageNumber(usageSummary?.cacheHits)} 次；最近 {formatUsageDate(usageSummary?.lastEventAt)}</small>
                      </div>
                    </div>
                    <div className="usage-pipeline-grid">
                      {pipelineUsage.map((item) => (
                        <div key={item.pipeline || "unknown"}>
                          <span>{String(item.pipeline || "unknown").toUpperCase()}</span>
                          <strong>{formatUsageNumber(item.requests)} 次</strong>
                          <small>{formatUsageNumber(item.totalTokens)} tokens</small>
                        </div>
                      ))}
                    </div>
                    <div className="token-usage-breakdown">
                      <div className="token-usage-card">
                        <div className="token-usage-card-title">
                          <strong>最近 7 天趋势</strong>
                          <span>按调用次数</span>
                        </div>
                        <div className="token-usage-bars">
                          {recentUsageDays.map((item) => (
                            <div className="token-usage-bar-row" key={item.date}>
                              <span>{item.date?.slice(5).replace("-", "/")}</span>
                              <div><i style={{ width: `${Math.max(3, (Number(item.requests || 0) / maxDayCalls) * 100)}%` }} /></div>
                              <strong>{formatUsageNumber(item.requests)} 次</strong>
                            </div>
                          ))}
                        </div>
                      </div>
                      <div className="token-usage-card">
                        <div className="token-usage-card-title">
                          <strong>端点调用</strong>
                          <span>Calls / Tokens</span>
                        </div>
                        <div className="token-usage-endpoints">
                          {endpointUsage.length === 0 ? (
                            <p>暂无调用数据。使用在线测试台或外部 agent 调用后会在这里出现。</p>
                          ) : endpointUsage.map((item) => (
                            <div key={`${item.pipeline || "v2"}-${item.endpoint}`}>
                              <code>{item.endpoint}</code>
                              <span>{String(item.pipeline || "v2").toUpperCase()} · {formatUsageNumber(item.requests)} 次</span>
                              <strong>{formatUsageNumber(item.totalTokens)} tokens</strong>
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
              <h2>接口总览</h2>
              <p className="docs-lead">
                V2 接口基于 <code>{REALGUARD_API_BASE}</code>；V1 图像模型基于 <code>{REALGUARD_V1_API_BASE}</code>。
                上传接口都使用 <code>multipart/form-data</code>，并传入 <code>X-RealGuard-Key</code>。
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
                <p>检查 V2 API 可用性、粗粒度能力、上传限制和访问保护状态。公开接口不会返回内部路径或详细阈值。</p>
                <div className="docs-code-block compact"><pre>{curlHealthExample}</pre></div>
              </div>

              <div id="admin-health" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-get">GET</span>
                  <h3>/admin/health</h3>
                </div>
                <p>受保护的详细诊断接口。启用访问令牌后，需要传入 <code>X-Jianzhen-Token</code>。</p>
                <div className="docs-code-block compact"><pre>{`curl -fsS ${REALGUARD_API_BASE}/admin/health \\
  -H "X-Jianzhen-Token: <token>"`}</pre></div>
              </div>

              <div id="detect" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/detect · V2</h3>
                </div>
                <p>核心鉴伪接口。上传文件后返回任务编号、鉴伪结论、置信度、证据摘要、模型版本和报告编号。默认上传上限为 25MB。</p>
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

              <div id="v1-detect" className="endpoint-detail">
                <div className="endpoint-heading">
                  <span className="method method-post">POST</span>
                  <h3>/api/developer/v1/detect · V1</h3>
                </div>
                <p>
                  V1 图像模型接口。适合需要复用旧 RealGuard 图像鉴伪链路的 agent 或业务系统。
                  请求使用 <code>file</code> 字段上传图片；平台会记录调用次数，但 V1 响应不返回 tokenUsage。
                </p>
                <h4>Response fields</h4>
                <div className="docs-table docs-table-2">
                  <strong>字段</strong><strong>说明</strong>
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
                <div className="docs-code-block">
                  <div className="docs-code-title"><span className="method method-post">POST</span><span>V1 Image Detect</span></div>
                  <pre>{curlV1DetectExample}</pre>
                </div>
              </div>
            </section>

            <section id="console" className="docs-section docs-console-section">
              <div className="docs-section-kicker">API Console</div>
              <h2>在线 API 测试台</h2>
              <p className="docs-lead">在网站内直接测试健康检查和鉴伪上传，验证 API Key、接口连通性、响应字段和耗时。</p>
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
                    onChange={(event) => {
                      const selected = event.target.files?.[0] || null;
                      if (selected) {
                        const validation = validateFile(selected, { kind: "测试文件", maxBytes: V2_CONSOLE_MAX_BYTES });
                        if (validation) {
                          setConsoleStatus({ tone: "error", text: validation });
                          event.target.value = "";
                          setTestFile(null);
                          return;
                        }
                      }
                      setConsoleStatus(selected ? { tone: "info", text: `已选择：${selected.name}` } : null);
                      setTestFile(selected);
                    }}
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
              <p className="docs-lead">外部 agent 输出时至少包含结论、置信度、证据摘要、版本和报告编号，避免只输出一句“真假”。V2 和 V1 的字段结构不同，必须按所选链路解析。</p>
              <h4>V2 字段</h4>
              <div className="developer-fields">
                {fields.map(([field, desc]) => (
                  <div key={field}>
                    <code>{field}</code>
                    <p>{desc}</p>
                  </div>
                ))}
              </div>
              <h4>V1 字段</h4>
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
                <a href="#v1-detect">
                  <span>V1 Image API</span>
                  <code>{REALGUARD_V1_API_BASE}/detect</code>
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
    if (next) {
      const message = validateFile(next, { kind: "图片", maxBytes: IMAGE_MAX_BYTES, mimePrefixes: ["image/"] });
      if (message) {
        setStatus({ tone: "error", text: message });
        return;
      }
    }
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

  function selectFile(next: File | null) {
    if (next) {
      const message = validateFile(next, { kind: "视频", maxBytes: VIDEO_MAX_BYTES, mimePrefixes: ["video/"] });
      if (message) {
        setStatus({ tone: "error", text: message });
        return;
      }
    }
    setFile(next);
    setResult(null);
    setStatus({ tone: "info", text: next ? `已选择: ${next.name}` : "等待上传视频或填写URL..." });
  }

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
            <UploadBox accept="video/*" file={file} onFile={selectFile} kind="视频" />
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
    setFile(null);
    setResults([]);
    setBaseUrl("");
    setStatus({ tone: "info", text: searchType === "image" ? "等待上传图片..." : "等待上传视频..." });
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
        kind: searchType === "image" ? "图片" : "视频",
        maxBytes: searchType === "image" ? IMAGE_MAX_BYTES : VIDEO_MAX_BYTES,
        mimePrefixes: [searchType === "image" ? "image/" : "video/"],
      });
      if (message) {
        setStatus({ tone: "error", text: message });
        return;
      }
    }
    setFile(next);
    setResults([]);
    setStatus({ tone: "info", text: next ? `已选择: ${next.name}` : "等待上传查询文件..." });
  }

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
            <UploadBox accept={searchType === "image" ? "image/*" : "video/*"} file={file} onFile={selectFile} kind={searchType === "image" ? "图片" : "视频"} />
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
  const [debouncedQuery, setDebouncedQuery] = useState(() => getInitialHistoryQuery());
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
      if (data.debug_code && import.meta.env.DEV) {
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

function validateFile(
  file: File,
  options: { kind: string; maxBytes: number; mimePrefixes?: string[] },
) {
  if (file.size > options.maxBytes) {
    return `${options.kind}不能超过 ${formatSize(options.maxBytes)}，当前文件为 ${formatSize(file.size)}。`;
  }
  if (options.mimePrefixes?.length && file.type) {
    const allowed = options.mimePrefixes.some((prefix) => file.type.startsWith(prefix));
    if (!allowed) return `请选择${options.kind}文件，当前文件类型为 ${file.type}。`;
  }
  return "";
}

function colorBg(color: string) {
  if (color === "var(--primary)") return "rgba(31,54,92,0.12)";
  if (color === "var(--primary-light)") return "rgba(92,114,154,0.14)";
  if (color === "var(--primary-dark)") return "rgba(17,24,39,0.12)";
  if (color === "var(--warning)") return "rgba(183,121,31,0.14)";
  if (color === "var(--accent)") return "rgba(201,42,42,0.13)";
  return "rgba(17,24,39,0.08)";
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
