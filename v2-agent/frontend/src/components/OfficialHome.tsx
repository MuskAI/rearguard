import {
  ArrowRight,
  BadgeCheck,
  BookOpen,
  Braces,
  ChevronDown,
  CircleHelp,
  Code2,
  FileCheck2,
  Fingerprint,
  Image as ImageIcon,
  Layers3,
  LockKeyhole,
  LogIn,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Waypoints,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { AccountUser } from "../api";
import AccountMenu from "./AccountMenu";
import HuijianBrand from "./HuijianBrand";

export type DeveloperEntry = "overview" | "tester" | "docs";

interface Props {
  authReady: boolean;
  user: AccountUser | null;
  onEnterWorkspace: () => void;
  onDeveloper: (entry?: DeveloperEntry) => void;
  onLogin: () => void;
  onLogout: () => void;
  onAnalyticsPreference: () => void;
}

const FAQS = [
  { question: "慧鉴AI 会直接替我做最终判断吗？", answer: "系统提供二元辅助结论，并同步展示最关键的证据与限制。新闻、司法、金融等高风险场景仍应核对原始来源并由专业人员复核。" },
  { question: "上传的文件会被其他用户看到吗？", answer: "不会。登录用户的任务、历史和报告按账号隔离；公开分享只会在你主动创建限时链接后发生，并且可以随时撤销。" },
  { question: "快速检测与 Soar 模式有什么区别？", answer: "快速检测适合日常筛查，优先返回主结论与关键水印线索；Soar 模式会调度更多独立证据源进行交叉复核，耗时更长。" },
  { question: "为什么结论还需要看证据？", answer: "任何检测都可能受压缩、裁剪、截图或未知生成器影响。证据能帮助你判断结论是否适用于当前文件，而不是只相信一个孤立分数。" },
  { question: "如何把鉴伪能力接入自己的产品？", answer: "登录开发者平台后可创建 API Key、查看多语言示例，并直接在网页调试台上传样本验证请求与响应。" },
] as const;

export default function OfficialHome({ authReady, user, onEnterWorkspace, onDeveloper, onLogin, onLogout, onAnalyticsPreference }: Props) {
  const [developerOpen, setDeveloperOpen] = useState(false);
  const developerRootRef = useRef<HTMLDivElement>(null);
  const developerTriggerRef = useRef<HTMLButtonElement>(null);

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

  function openDeveloper(entry: DeveloperEntry) {
    setDeveloperOpen(false);
    onDeveloper(entry);
  }

  return (
    <div className="official-site home-vnext">
      <header className="home-header">
        <a className="home-brand-link" href="#home" aria-label="返回慧鉴AI官网首页"><HuijianBrand /></a>
        <nav className="home-nav" aria-label="官网导航">
          <div
            ref={developerRootRef}
            className={`home-developer-menu ${developerOpen ? "is-open" : ""}`}
            onPointerEnter={() => setDeveloperOpen(true)}
            onPointerLeave={(event) => {
              if (event.pointerType === "mouse") setDeveloperOpen(false);
            }}
          >
            <button ref={developerTriggerRef} type="button" aria-haspopup="menu" aria-expanded={developerOpen} onClick={() => setDeveloperOpen((value) => !value)}>
              <Code2 size={17} /><span className="home-label-wide">开发者平台</span><span className="home-label-compact">开发者</span><ChevronDown size={14} />
            </button>
            {developerOpen && (
              <div className="home-developer-popover" role="menu" aria-label="开发者平台入口">
                <button type="button" role="menuitem" onClick={() => openDeveloper("overview")}><span><Braces size={18} /></span><div><strong>平台概览</strong><small>额度、调用与账户</small></div><ArrowRight size={15} /></button>
                <button type="button" role="menuitem" onClick={() => openDeveloper("tester")}><span><Sparkles size={18} /></span><div><strong>在线调试</strong><small>在网页中发送真实请求</small></div><ArrowRight size={15} /></button>
                <button type="button" role="menuitem" onClick={() => openDeveloper("docs")}><span><BookOpen size={18} /></span><div><strong>接入文档</strong><small>多语言示例与错误码</small></div><ArrowRight size={15} /></button>
              </div>
            )}
          </div>
          <a className="home-faq-link" href="#faq"><CircleHelp size={17} /><span>常见问题</span></a>
        </nav>
        <div className="home-header-actions">
          {authReady && (user ? (
            <AccountMenu user={user} onWorkspace={onEnterWorkspace} onDeveloper={() => onDeveloper("overview")} onLogout={onLogout} />
          ) : (
            <button type="button" className="home-login-button" onClick={onLogin}><LogIn size={17} /><span>登录</span></button>
          ))}
          <button type="button" className="home-workspace-button" onClick={onEnterWorkspace}><span className="home-label-wide">开始鉴伪</span><span className="home-label-compact">鉴伪</span><ArrowRight size={17} /></button>
        </div>
      </header>

      <main>
        <section className="home-hero" id="home" aria-labelledby="official-home-title">
          <div className="home-hero-copy">
            <p className="home-eyebrow"><span><ShieldCheck size={15} /></span> 面向真实世界的内容鉴伪</p>
            <h1 id="official-home-title" tabIndex={-1}>慧鉴AI</h1>
            <h2>把真假判断，变成一条<br />看得懂的证据链。</h2>
            <p className="home-hero-description">从模型判断、可见水印到来源信息，在一个任务里给出明确结论、关键依据与可追溯报告。</p>
            <div className="home-hero-actions">
              <button type="button" onClick={onEnterWorkspace}>上传内容开始鉴伪 <ArrowRight size={19} /></button>
              <a href="#how-it-works">看看证据如何形成 <ChevronDown size={18} /></a>
            </div>
            <div className="home-hero-assurances" aria-label="服务特性">
              <span><BadgeCheck size={15} /> 真实检测链路</span>
              <span><LockKeyhole size={15} /> 账号数据隔离</span>
              <span><FileCheck2 size={15} /> 结果可复核</span>
            </div>
          </div>
          <figure className="home-hero-visual" aria-label="慧鉴AI品牌助手小鉴正在整理内容证据">
            <div className="home-scan-field" aria-hidden="true">
              <i className="scan-line-a" /><i className="scan-line-b" /><i className="scan-line-c" />
              <span className="home-evidence-chip chip-model"><ScanSearch size={15} /> 真实性分析 <b>完成</b></span>
              <span className="home-evidence-chip chip-source"><Fingerprint size={15} /> 来源线索 <b>核验中</b></span>
              <span className="home-evidence-chip chip-report"><FileCheck2 size={15} /> 证据报告 <b>可追溯</b></span>
            </div>
            <img src="/brand/huijian-mascot.webp" alt="慧鉴AI品牌助手小鉴" width="594" height="800" />
          </figure>
        </section>

        <section className="home-proof-strip" aria-label="慧鉴AI核心能力">
          <article><span>01</span><div><strong>给出明确结论</strong><small>真假判断不绕弯</small></div></article>
          <article><span>02</span><div><strong>突出关键证据</strong><small>重要线索先看见</small></div></article>
          <article><span>03</span><div><strong>保留原始信息</strong><small>元数据完整呈现</small></div></article>
        </section>

        <section className="home-capabilities" id="how-it-works" aria-labelledby="home-capabilities-title">
          <div className="home-section-heading">
            <p>一份内容，三条证据路径</p>
            <h2 id="home-capabilities-title">结论先到，依据紧随其后。</h2>
            <span>系统会按文件类型组织证据，不把复杂的技术实现留给用户理解。</span>
          </div>
          <div className="home-capability-grid">
            <article><span className="home-capability-icon"><ScanSearch size={24} /></span><small>主判断</small><h3>真实性分析</h3><p>识别生成痕迹与局部异常，形成真假风险基线。</p><b>01</b></article>
            <article><span className="home-capability-icon"><Fingerprint size={24} /></span><small>强线索</small><h3>水印与来源</h3><p>定位可见水印，核对平台标记、拍摄信息与内容凭证。</p><b>02</b></article>
            <article><span className="home-capability-icon"><Layers3 size={24} /></span><small>可复核</small><h3>证据汇总</h3><p>把支持、冲突与限制放进同一份报告，便于再次核查。</p><b>03</b></article>
          </div>
        </section>

        <section className="home-evidence-story" aria-labelledby="home-evidence-title">
          <figure><img src="/brand/huijian-evidence-studio.webp" alt="小鉴正在整理图像、视频与文档证据" width="1536" height="1024" loading="lazy" /></figure>
          <div>
            <p>统一 Agent 工作台</p>
            <h2 id="home-evidence-title">不用在不同版本和入口之间来回切换。</h2>
            <ol>
              <li><span><ImageIcon size={17} /></span><div><strong>上传内容</strong><small>拖入图片、视频或文档</small></div></li>
              <li><span><Waypoints size={17} /></span><div><strong>观察进度</strong><small>只展示用户真正关心的阶段</small></div></li>
              <li><span><FileCheck2 size={17} /></span><div><strong>复核证据</strong><small>放大原图并查看完整文件信息</small></div></li>
            </ol>
            <button type="button" onClick={onEnterWorkspace}>进入统一工作台 <ArrowRight size={18} /></button>
          </div>
        </section>

        <section className="home-faq" id="faq" aria-labelledby="home-faq-title">
          <div className="home-section-heading">
            <p>常见问题</p>
            <h2 id="home-faq-title">开始之前，先把边界说清楚。</h2>
          </div>
          <div className="home-faq-list">
            {FAQS.map((item, index) => (
              <details key={item.question} name="huijian-faq">
                <summary><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.question}</strong><i><ChevronDown size={18} /></i></summary>
                <p>{item.answer}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="home-final-cta" aria-labelledby="home-final-title">
          <div><p>从第一份内容开始</p><h2 id="home-final-title">让每个判断，都能找到依据。</h2></div>
          <button type="button" onClick={onEnterWorkspace}>开始鉴伪 <ArrowRight size={20} /></button>
        </section>
      </main>

      <footer className="home-footer">
        <HuijianBrand compact />
        <p>慧鉴AI 提供数字内容鉴伪辅助分析，不替代专业机构与人工最终判断。</p>
        <div><a href="/legal/terms.html" target="_blank" rel="noreferrer">用户协议</a><a href="/legal/privacy.html" target="_blank" rel="noreferrer">隐私政策</a><button type="button" onClick={onAnalyticsPreference}>匿名统计偏好</button><a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">浙ICP备2026051442号</a></div>
      </footer>
    </div>
  );
}
