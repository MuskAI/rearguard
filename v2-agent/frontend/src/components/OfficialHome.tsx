import {
  ArrowRight,
  ChevronDown,
  LogIn,
  Menu,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { AccountUser } from "../api";
import AccountMenu from "./AccountMenu";
import BrandArtIcon, { BrandArtIconName } from "./BrandArtIcon";
import HuijianBrand from "./HuijianBrand";
import Presence from "./Presence";

export type DeveloperEntry = "overview" | "tester" | "docs";

interface Props {
  authReady: boolean;
  user: AccountUser | null;
  analyticsEnabled: boolean;
  onEnterWorkspace: () => void;
  onDeveloper: (entry?: DeveloperEntry) => void;
  onLogin: () => void;
  onLogout: () => void;
  onToggleAnalytics: () => void;
}

const NAV_ITEMS = [
  { label: "产品能力", href: "#capabilities" },
  { label: "应用场景", href: "#scenarios" },
  { label: "工作方式", href: "#workflow" },
] as const;

const FAQS = [
  { question: "慧鉴AI 会直接给出真假结论吗？", answer: "会。系统先给出 Real 或 Fake 的二元辅助结论，再展示支持结论的关键证据、冲突和局限。新闻、司法、金融等高风险场景仍应结合原始来源复核。" },
  { question: "快速检测与 Swarm 模式有什么区别？", answer: "快速检测面向日常筛查，同步核验真实性信号与 AI 水印；Swarm 会调度更多独立证据源进行交叉复核，适合难例与重要内容。" },
  { question: "手机拍摄照片会被轻易判假吗？", answer: "系统会读取相机型号、拍摄参数和其他原始元数据，将可信拍摄链路作为倾向真实的证据，同时说明这些信息可能被修改，避免把单一字段当成绝对证明。" },
  { question: "上传的文件会被其他用户看到吗？", answer: "不会。登录账号的任务、历史和报告按账号隔离；只有你主动创建限时分享链接时，指定报告才会被访问，并且可以撤销。" },
  { question: "如何把鉴伪能力接入自己的产品？", answer: "登录开发者平台后可以创建 API Key、查看计费和用量、复制多语言示例，并在网页调试台发送真实请求。" },
] as const;

const CAPABILITIES: Array<{
  number: string;
  eyebrow: string;
  title: string;
  description: string;
  icon: BrandArtIconName;
  tags: string[];
}> = [
  {
    number: "01",
    eyebrow: "FAST CHECK",
    title: "快速检测",
    description: "一次完成真实性分析与 AI 水印核验，先给明确结论，再呈现最重要的依据。",
    icon: "fast",
    tags: ["日常筛查", "水印同步", "快速返回"],
  },
  {
    number: "02",
    eyebrow: "SWARM REVIEW",
    title: "Swarm 蜂群复核",
    description: "调度多条独立证据链并行判断，把一致意见、证据冲突和不确定性放在同一份结果中。",
    icon: "swarm",
    tags: ["多源交叉", "难例复核", "并行分析"],
  },
  {
    number: "03",
    eyebrow: "EVIDENCE REPORT",
    title: "证据报告",
    description: "保留水印位置、原始元数据与关键判断依据，让结果可以被再次检查、归档和分享。",
    icon: "report",
    tags: ["水印标注", "完整元数据", "报告归档"],
  },
];

const SCENARIOS: Array<{ icon: BrandArtIconName; title: string; description: string }> = [
  { icon: "image", title: "媒体内容核验", description: "在发布与引用之前，快速检查图片和视频来源风险。" },
  { icon: "document", title: "教学与科研", description: "批量评测数据集，观察准确率、分数与资源消耗。" },
  { icon: "workflow", title: "平台内容治理", description: "通过 API 接入审核流程，统一记录调用与检测结果。" },
  { icon: "developer", title: "品牌与合规", description: "定位平台水印和来源线索，为人工复核提供上下文。" },
];

export default function OfficialHome({
  authReady,
  user,
  analyticsEnabled,
  onEnterWorkspace,
  onDeveloper,
  onLogin,
  onLogout,
  onToggleAnalytics,
}: Props) {
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const siteRef = useRef<HTMLDivElement>(null);
  const developerRootRef = useRef<HTMLDivElement>(null);
  const developerTriggerRef = useRef<HTMLButtonElement>(null);
  const developerMenuRef = useRef<HTMLDivElement>(null);
  const developerFocusIndexRef = useRef<number | null>(null);
  const mobileNavRef = useRef<HTMLElement>(null);
  const mobileNavTriggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const root = siteRef.current;
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-reveal]"));
    if (!("IntersectionObserver" in window)) {
      nodes.forEach((node) => node.classList.add("is-visible"));
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.14, rootMargin: "0px 0px -7% 0px" });
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!developerOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!developerRootRef.current?.contains(event.target as Node)) setDeveloperOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setDeveloperOpen(false);
      developerTriggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [developerOpen]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!mobileNavRef.current?.contains(target) && !mobileNavTriggerRef.current?.contains(target)) {
        setMobileNavOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMobileNavOpen(false);
      mobileNavTriggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [mobileNavOpen]);

  function openDeveloper(entry: DeveloperEntry) {
    setDeveloperOpen(false);
    setMobileNavOpen(false);
    onDeveloper(entry);
  }

  return (
    <div ref={siteRef} className="official-site home-vnext home-v3">
      <header className="home-header">
        <a className="home-brand-link" href="#home" aria-label="返回慧鉴AI官网首页"><HuijianBrand /></a>

        <nav className="home-nav home-desktop-nav" aria-label="官网导航">
          {NAV_ITEMS.map((item) => <a key={item.href} href={item.href}>{item.label}</a>)}
          <div
            ref={developerRootRef}
            className={`home-developer-menu ${developerOpen ? "is-open" : ""}`}
            onPointerEnter={() => setDeveloperOpen(true)}
            onPointerLeave={(event) => { if (event.pointerType === "mouse") setDeveloperOpen(false); }}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node)) setDeveloperOpen(false);
            }}
          >
            <button
              ref={developerTriggerRef}
              type="button"
              aria-haspopup="menu"
              aria-expanded={developerOpen}
              aria-controls="home-developer-navigation"
              onClick={() => setDeveloperOpen((value) => !value)}
              onKeyDown={(event) => {
                if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
                event.preventDefault();
                developerFocusIndexRef.current = event.key === "ArrowUp" ? -1 : 0;
                setDeveloperOpen(true);
              }}
            >
              <span>开发者平台</span><ChevronDown size={14} />
            </button>
            <Presence
              present={developerOpen}
              onEnterComplete={() => {
                const requested = developerFocusIndexRef.current;
                if (requested == null) return;
                const items = developerMenuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
                const index = requested < 0 ? (items?.length || 1) - 1 : requested;
                items?.[index]?.focus();
                developerFocusIndexRef.current = null;
              }}
            >
              {(phase) => (
                <div
                  ref={developerMenuRef}
                  id="home-developer-navigation"
                  className="home-developer-popover"
                  role="menu"
                  aria-label="开发者平台入口"
                  aria-hidden={!developerOpen}
                  data-presence={phase}
                  onKeyDown={(event) => {
                    if (!["ArrowDown", "ArrowUp", "Home", "End", "Escape"].includes(event.key)) return;
                    event.preventDefault();
                    if (event.key === "Escape") {
                      setDeveloperOpen(false);
                      developerTriggerRef.current?.focus();
                      return;
                    }
                    const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'));
                    const current = items.indexOf(document.activeElement as HTMLButtonElement);
                    const next = event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? items.length - 1
                        : (Math.max(current, 0) + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
                    items[next]?.focus();
                  }}
                >
                  <button type="button" role="menuitem" tabIndex={developerOpen ? 0 : -1} onClick={() => openDeveloper("overview")}><BrandArtIcon name="developer" /><div><strong>平台概览</strong><small>API Key、额度与调用</small></div><ArrowRight size={15} /></button>
                  <button type="button" role="menuitem" tabIndex={-1} onClick={() => openDeveloper("tester")}><BrandArtIcon name="fast" /><div><strong>在线调试</strong><small>发送真实检测请求</small></div><ArrowRight size={15} /></button>
                  <button type="button" role="menuitem" tabIndex={-1} onClick={() => openDeveloper("docs")}><BrandArtIcon name="report" /><div><strong>接入文档</strong><small>多语言示例与错误码</small></div><ArrowRight size={15} /></button>
                </div>
              )}
            </Presence>
          </div>
          <a href="#faq">常见问题</a>
        </nav>

        <div className="home-header-actions">
          <button ref={mobileNavTriggerRef} type="button" className="home-mobile-menu-button" onClick={() => setMobileNavOpen((value) => !value)} aria-label={mobileNavOpen ? "关闭网站导航" : "打开网站导航"} aria-expanded={mobileNavOpen} aria-controls="home-mobile-navigation">
            {mobileNavOpen ? <X size={19} /> : <Menu size={19} />}
          </button>
          {authReady && (user ? (
            <AccountMenu user={user} onWorkspace={onEnterWorkspace} onDeveloper={() => onDeveloper("overview")} onLogout={onLogout} />
          ) : (
            <button type="button" className="home-login-button" onClick={onLogin} aria-label="登录账号" title="登录账号"><LogIn size={17} /><span>登录</span></button>
          ))}
          <button type="button" className="home-workspace-button" onClick={onEnterWorkspace}><span className="home-label-wide">开始鉴伪</span><span className="home-label-compact">鉴伪</span><ArrowRight size={17} /></button>
        </div>

        <Presence
          present={mobileNavOpen}
          onEnterComplete={() => {
            mobileNavRef.current?.querySelector<HTMLElement>("a[href], button:not([disabled])")?.focus();
          }}
        >
          {(phase) => (
            <nav ref={mobileNavRef} id="home-mobile-navigation" className="home-mobile-nav" aria-label="移动端官网导航" aria-hidden={!mobileNavOpen} data-presence={phase}>
              {NAV_ITEMS.map((item) => <a key={item.href} href={item.href} tabIndex={mobileNavOpen ? 0 : -1} onClick={() => setMobileNavOpen(false)}>{item.label}<ArrowRight size={16} /></a>)}
              <button type="button" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => openDeveloper("overview")}>开发者概览<ArrowRight size={16} /></button>
              <button type="button" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => openDeveloper("tester")}>在线调试<ArrowRight size={16} /></button>
              <button type="button" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => openDeveloper("docs")}>接入文档<ArrowRight size={16} /></button>
              <a href="#faq" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => setMobileNavOpen(false)}>常见问题<ArrowRight size={16} /></a>
            </nav>
          )}
        </Presence>
      </header>

      <main>
        <section className="home-hero" id="home" aria-labelledby="official-home-title">
          <div className="home-hero-copy" data-reveal>
            <p className="home-eyebrow"><i /> AI 内容真实性基础设施</p>
            <h1 id="official-home-title" tabIndex={-1}>慧鉴AI</h1>
            <h2>把真假判断，落成<br />可以复核的证据。</h2>
            <p className="home-hero-description">从快速筛查到 Swarm 蜂群复核，把水印、元数据与多源判断组织成一份真正看得懂的结论。</p>
            <div className="home-hero-actions">
              <button type="button" onClick={onEnterWorkspace}>上传内容开始鉴伪 <ArrowRight size={19} /></button>
              <a href="#capabilities">探索产品能力 <ChevronDown size={18} /></a>
            </div>
          </div>

          <figure className="home-hero-visual companion-hero-visual" aria-label="图片、视频与文档经过小鉴核验后形成证据" data-reveal>
            <div className="home-hero-visual-stage">
              <img src="/brand/huijian-companion-evidence-flow-c.webp" alt="图片、视频和文档进入小鉴核验流程并形成证据结果" width="1120" height="1400" />
              <span className="hero-art-token token-fast"><BrandArtIcon name="fast" /><b>快速检测</b></span>
              <span className="hero-art-token token-swarm"><BrandArtIcon name="swarm" /><b>Swarm</b></span>
              <span className="hero-art-token token-report"><BrandArtIcon name="report" /><b>证据报告</b></span>
            </div>
          </figure>
        </section>

        <section className="home-value-rail" aria-label="慧鉴AI核心价值" data-reveal>
          <article><strong>图片 · 视频 · 文档</strong><span>一个入口自动分流</span></article>
          <article><strong>快速 · Swarm</strong><span>按任务重要程度选择</span></article>
          <article><strong>水印 · 元数据 · 报告</strong><span>关键证据集中呈现</span></article>
        </section>

        <section className="home-capabilities" id="capabilities" aria-labelledby="home-capabilities-title">
          <div className="home-section-heading" data-reveal>
            <p>产品能力</p>
            <h2 id="home-capabilities-title">不是多堆几个分数，<br />而是让每条证据各就其位。</h2>
            <span>根据任务风险选择分析深度，所有模式都给出明确结论和可核验依据。</span>
          </div>
          <div className="home-capability-list">
            {CAPABILITIES.map((item) => (
              <article key={item.number} data-reveal>
                <span className="home-capability-number">{item.number}</span>
                <BrandArtIcon name={item.icon} className="home-capability-art" />
                <div className="home-capability-copy">
                  <small>{item.eyebrow}</small>
                  <h3>{item.title}</h3>
                  <p>{item.description}</p>
                  <ul>{item.tags.map((tag) => <li key={tag}>{tag}</li>)}</ul>
                </div>
                <button type="button" onClick={onEnterWorkspace} aria-label={`使用${item.title}`}><ArrowRight size={22} /></button>
              </article>
            ))}
          </div>
        </section>

        <section className="home-scenarios" id="scenarios" aria-labelledby="home-scenarios-title">
          <div className="home-section-heading" data-reveal>
            <p>应用场景</p>
            <h2 id="home-scenarios-title">从单张核验，到批量治理。</h2>
            <span>面向需要判断内容来源、规模化测试或接入审核流程的真实工作。</span>
          </div>
          <div className="home-scenario-grid">
            {SCENARIOS.map((item) => (
              <article key={item.title} data-reveal>
                <BrandArtIcon name={item.icon} />
                <div><h3>{item.title}</h3><p>{item.description}</p></div>
                <ArrowRight size={19} aria-hidden="true" />
              </article>
            ))}
          </div>
        </section>

        <section className="home-workflow" id="workflow" aria-labelledby="home-workflow-title">
          <div className="home-workflow-heading" data-reveal>
            <p>工作方式</p>
            <h2 id="home-workflow-title">三步完成一次<br />可复核判断。</h2>
            <button type="button" onClick={onEnterWorkspace}>进入统一工作台 <ArrowRight size={18} /></button>
          </div>
          <ol>
            <li data-reveal><span>01</span><BrandArtIcon name="image" /><div><strong>提交内容</strong><p>上传或拖入图片、视频和文档，系统自动识别处理链路。</p></div></li>
            <li data-reveal><span>02</span><BrandArtIcon name="workflow" /><div><strong>观察分析</strong><p>只展示用户真正关心的阶段，重要证据优先出现。</p></div></li>
            <li data-reveal><span>03</span><BrandArtIcon name="report" /><div><strong>复核与归档</strong><p>放大查看水印区域，核对元数据并下载完整报告。</p></div></li>
          </ol>
        </section>

        <section className="home-faq" id="faq" aria-labelledby="home-faq-title">
          <div className="home-section-heading" data-reveal>
            <BrandArtIcon name="faq" className="home-faq-art" />
            <p>常见问题</p>
            <h2 id="home-faq-title">开始之前，把能力与边界说清楚。</h2>
          </div>
          <div className="home-faq-list" data-reveal>
            {FAQS.map((item, index) => (
              <details key={item.question} name="huijian-faq">
                <summary><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.question}</strong><i><ChevronDown size={19} /></i></summary>
                <p>{item.answer}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="home-final-cta" aria-labelledby="home-final-title" data-reveal>
          <BrandArtIcon name="fast" />
          <div><p>从第一份内容开始</p><h2 id="home-final-title">让每个判断，都能找到依据。</h2></div>
          <button type="button" onClick={onEnterWorkspace}>开始鉴伪 <ArrowRight size={20} /></button>
        </section>
      </main>

      <footer className="home-footer">
        <HuijianBrand compact />
        <p>慧鉴AI 提供数字内容鉴伪辅助分析，不替代专业机构与人工最终判断。</p>
        <div>
          <a href="/legal/terms.html" target="_blank" rel="noreferrer">用户协议</a>
          <a href="/legal/privacy.html" target="_blank" rel="noreferrer">隐私政策</a>
          <button type="button" onClick={onToggleAnalytics} aria-pressed={analyticsEnabled}>匿名统计：{analyticsEnabled ? "已开启" : "已关闭"}</button>
          <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">浙ICP备2026051442号</a>
        </div>
      </footer>
    </div>
  );
}
