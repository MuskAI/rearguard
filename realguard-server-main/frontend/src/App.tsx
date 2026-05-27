import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import {
  Counters,
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
  sendSmsCode
} from "./api";

type PageKey = "home" | "image" | "video" | "retrieve" | "history";
type Status = { tone: "ok" | "error" | "info"; text: string } | null;
type AuthMode = "password" | "sms" | "register";
type HistoryTabKey = "image" | "video" | "imageRetrieve" | "videoRetrieve";
type HistoryFilterKey = "all" | "guest" | "metadata" | "issues" | "ai";

const emptyCounters: Counters = {
  image_detect: 0,
  video_detect: 0,
  image_retrieve: 0,
  video_retrieve: 0
};

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
            <span className="logo-full">数字内容侵权、分析、检索和存证综合平台</span>
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
          <button onClick={authAction}><i className={`fa ${user ? "fa-sign-out" : "fa-user"}`} /> {user ? "退出登录" : "登录/注册"}</button>
        </div>
      </header>
      <nav className="mobile-bottom-nav">
        <button className={page === "home" ? "active" : ""} onClick={() => go("home")}><i className="fa fa-home" /><span>首页</span></button>
        <button className={page === "image" ? "active" : ""} onClick={() => go("image")}><i className="fa fa-image" /><span>图像</span></button>
        <button className={page === "video" ? "active" : ""} onClick={() => go("video")}><i className="fa fa-film" /><span>视频</span></button>
        <button className={page === "retrieve" ? "active" : ""} onClick={() => go("retrieve")}><i className="fa fa-search" /><span>检索</span></button>
        <button className={page === "history" ? "active" : ""} onClick={() => go("history")}><i className="fa fa-clock-o" /><span>历史</span></button>
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

  useEffect(() => {
    setStatus({ tone: "info", text: "正在加载历史记录" });
    const request =
      tab === "image"
        ? getHistory("image-detections")
        : tab === "video"
          ? getHistory("video-detections")
          : getRetrievalHistory(tab === "imageRetrieve" ? "image" : "video");
    request
      .then((data) => {
        setRecords(data.records || []);
        setStatus(null);
      })
      .catch((error) => {
        setRecords([]);
        setStatus({ tone: "error", text: errorMessage(error) });
      });
  }, [tab]);

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
    if (filter !== "all" && isHistoryFilterSupported(tab, filter)) params.set("historyFilter", filter);
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

  const queriedRecords = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return records;
    return records.filter((record) => {
      const fields = [
        String(record.filename || ""),
        String(record.final_label || ""),
        String(record.confidence || ""),
        String(record.createtime || ""),
        String(record.top_k || ""),
      ];
      return fields.some((field) => field.toLowerCase().includes(q));
    });
  }, [records, query]);

  const filteredRecords = useMemo(() => {
    if (tab === "image") {
      if (filter === "guest") return queriedRecords.filter((record) => Boolean(record.is_guest_record));
      if (filter === "metadata") return queriedRecords.filter((record) => Boolean(record.has_metadata));
      if (filter === "issues") return queriedRecords.filter((record) => Boolean(record.has_visual_issues));
      return queriedRecords;
    }
    if (tab === "video") {
      if (filter === "guest") return queriedRecords.filter((record) => Boolean(record.is_guest_record));
      if (filter === "ai") return queriedRecords.filter((record) => String(record.final_label || "").includes("AI"));
      return queriedRecords;
    }
    return queriedRecords;
  }, [queriedRecords, tab, filter]);

  const summaryCards = useMemo(() => {
    const baseRecords = filteredRecords;
    if (tab === "image") {
      return [
        { label: "当前记录", value: baseRecords.length },
        { label: "访客记录", value: baseRecords.filter((record) => Boolean(record.is_guest_record)).length },
        { label: "带元数据", value: baseRecords.filter((record) => Boolean(record.has_metadata)).length },
        { label: "有可疑点", value: baseRecords.filter((record) => Boolean(record.has_visual_issues)).length },
      ];
    }
    if (tab === "video") {
      return [
        { label: "当前记录", value: baseRecords.length },
        { label: "访客记录", value: baseRecords.filter((record) => Boolean(record.is_guest_record)).length },
        { label: "AI结论", value: baseRecords.filter((record) => String(record.final_label || "").includes("AI")).length },
        { label: "真实结论", value: baseRecords.filter((record) => String(record.final_label || "").includes("真实")).length },
      ];
    }
    const resultCount = baseRecords.reduce((sum, record) => sum + Number(record.result_count || 0), 0);
    const topKAvg = baseRecords.length
      ? Math.round((baseRecords.reduce((sum, record) => sum + Number(record.top_k || 0), 0) / baseRecords.length) * 10) / 10
      : 0;
    return [
      { label: "当前查询", value: baseRecords.length },
      { label: "命中总数", value: resultCount },
      { label: "平均Top-K", value: topKAvg },
      { label: "查询类型", value: tab === "imageRetrieve" ? "图像" : "视频" },
    ];
  }, [filteredRecords, tab]);

  const filterOptions = getHistoryFilterOptions(tab);
  const activeSummary = getHistoryActiveSummary(tab, filter, query);
  const matchSummary =
    filteredRecords.length === records.length
      ? `当前展示 ${filteredRecords.length} 条记录`
      : `当前匹配 ${filteredRecords.length} / ${records.length} 条记录`;

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
            <button className={`model-tab ${tab === "image" ? "active" : ""}`} onClick={() => setTab("image")}>图像鉴伪</button>
            <button className={`model-tab ${tab === "video" ? "active" : ""}`} onClick={() => setTab("video")}>视频鉴伪</button>
            <button className={`model-tab ${tab === "imageRetrieve" ? "active" : ""}`} onClick={() => setTab("imageRetrieve")}>图像检索</button>
            <button className={`model-tab ${tab === "videoRetrieve" ? "active" : ""}`} onClick={() => setTab("videoRetrieve")}>视频检索</button>
          </div>
          {status && <div className={`notice ${status.tone}`}>{status.text}</div>}
          {records.length ? (
            <>
              <div className="history-summary-grid">
                {summaryCards.map((card) => (
                  <div key={card.label} className="history-summary-card">
                    <span>{card.label}</span>
                    <strong>{card.value}</strong>
                  </div>
                ))}
              </div>
              <div className="history-search-bar">
                <div className="input-wrap">
                  <i className="fa fa-search" />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="按文件名、结论、时间搜索历史记录"
                  />
                </div>
                {query && (
                  <button type="button" className="btn-code history-search-clear" onClick={() => setQuery("")}>
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
                      setFilter("all");
                      setQuery("");
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
                      onClick={() => setFilter(option.key)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
              {filteredRecords.length ? (
                <HistoryRecords records={filteredRecords} tab={tab} query={query} />
              ) : (
                <EmptyState
                  icon="fa-filter"
                  text="当前筛选条件下暂无记录"
                  actions={[
                    { label: "清除条件", onClick: () => { setFilter("all"); setQuery(""); } },
                    { label: tab === "video" ? "去视频鉴伪" : tab === "image" ? "去图像鉴伪" : "去侵权检索", onClick: () => setPage(tab === "video" ? "video" : tab === "image" ? "image" : "retrieve") },
                  ]}
                />
              )}
            </>
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
  return (
    <div className="history-grid">
      {records.map((record, index) => {
        const mediaUrl = historyMediaUrl(record);
        const previewUrl = historyPreviewUrl(record) || mediaUrl;
        const title = String(record.filename || `历史记录 ${index + 1}`);
        const verdict = String(record.final_label || (record.result_count ? `${record.result_count} 条结果` : "-"));
        const meta = String(record.confidence || record.top_k || "-");
        const reportUrl = String(record.report_url || "");
        const guestRecord = Boolean(record.is_guest_record);
        const hasMetadata = Boolean(record.has_metadata);
        const hasIssues = Boolean(record.has_visual_issues);
        const issueCount = Number(record.visual_issue_count || 0);
        const timeText = String(record.createtime || "-");
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
                  <span className="history-tag guest"><i className="fa fa-user-secret" /> 访客</span>
                  {hasMetadata && <span className="history-tag meta"><i className="fa fa-info-circle" /> 元数据</span>}
                  {hasIssues && <span className="history-tag issue"><i className="fa fa-exclamation-triangle" /> 可疑点{issueCount > 0 ? ` ${issueCount}` : ""}</span>}
                </div>
              )}
              {!guestRecord && (hasMetadata || hasIssues) && (
                <div className="history-tags">
                  {hasMetadata && <span className="history-tag meta"><i className="fa fa-info-circle" /> 元数据</span>}
                  {hasIssues && <span className="history-tag issue"><i className="fa fa-exclamation-triangle" /> 可疑点{issueCount > 0 ? ` ${issueCount}` : ""}</span>}
                </div>
              )}
              <div className="history-row"><span>时间</span><strong>{renderHighlightedText(timeText, query)}</strong></div>
              <div className="history-row"><span>{record.result_count ? "数量" : "结论"}</span><strong>{renderHighlightedText(verdict, query)}</strong></div>
              <div className="history-row"><span>{record.top_k ? "Top-K" : "置信度"}</span><strong>{renderHighlightedText(meta, query)}</strong></div>
              {!record.result_count && reportUrl && (
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
              {!!record.result_count && reportUrl && (
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
      <div className="footer-logo"><i className="fa fa-eye" /> 数字内容侵权、分析、检索和存证综合平台</div>
      <p className="footer-copy">&copy; 2025 数字内容侵权、分析、检索和存证综合平台</p>
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
  return value && ["home", "image", "video", "retrieve", "history"].includes(value) ? value : "home";
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
    ];
  }
  return [];
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
