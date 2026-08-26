import {
  ArrowRight,
  Building2,
  CheckCircle2,
  FlaskConical,
  Handshake,
  Landmark,
  LockKeyhole,
  LogIn,
  Menu,
  Network,
  Send,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import type { AccountUser, CollaborationInquiryInput } from "../api";
import { submitCollaborationInquiry } from "../api";
import AccountMenu from "./AccountMenu";
import HuijianBrand from "./HuijianBrand";
import Presence from "./Presence";

interface Props {
  authReady: boolean;
  user: AccountUser | null;
  onHome: () => void;
  onWorkspace: () => void;
  onPlayground: () => void;
  onDeveloper: () => void;
  onLogin: () => void;
  onLogout: () => void;
}

type FormState = CollaborationInquiryInput;
type SubmitState = "idle" | "submitting" | "success" | "error";

const INITIAL_FORM: FormState = {
  collaborationType: "research",
  name: "",
  organization: "",
  contact: "",
  message: "",
  website: "",
  privacyAccepted: false,
};

const COLLABORATION_AREAS = [
  {
    icon: FlaskConical,
    audience: "高校 · 实验室 · 研究团队",
    title: "联合研究与科学评测",
    description: "围绕真实场景难例、模型泛化、可解释证据与评测方法，开展可复现的联合研究。",
  },
  {
    icon: Building2,
    audience: "媒体 · 内容平台 · 品牌机构",
    title: "内容真实性治理",
    description: "把鉴伪能力接入审核流程，共同设计适合业务风险等级的人机协作方案。",
  },
  {
    icon: Landmark,
    audience: "数据机构 · 公共服务 · 行业伙伴",
    title: "数据与基准共建",
    description: "在授权、隐私与可追溯前提下，共建更接近真实传播环境的数据和评价基准。",
  },
  {
    icon: Network,
    audience: "Agent · SaaS · 开发者生态",
    title: "产品与能力接入",
    description: "通过 API 与 Agent Skill 将检测、证据解释和报告能力嵌入现有产品。",
  },
] as const;

const PROCESS = [
  { number: "01", title: "说清问题", copy: "告诉我们你的使用场景、样本特点和希望验证的目标。" },
  { number: "02", title: "共同验证", copy: "先用小规模真实数据验证效果、成本与系统边界。" },
  { number: "03", title: "确定合作", copy: "对齐数据规范、交付方式与双方责任，再进入长期协作。" },
] as const;

function createRequestKey() {
  return globalThis.crypto?.randomUUID?.() || `collab-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function AboutCooperation({
  authReady,
  user,
  onHome,
  onWorkspace,
  onPlayground,
  onDeveloper,
  onLogin,
  onLogout,
}: Props) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [submitMessage, setSubmitMessage] = useState("");
  const [inquiryId, setInquiryId] = useState("");
  const pageRef = useRef<HTMLDivElement>(null);
  const mobileNavRef = useRef<HTMLElement>(null);
  const mobileTriggerRef = useRef<HTMLButtonElement>(null);
  const requestKeyRef = useRef("");

  useEffect(() => {
    const root = pageRef.current;
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-about-reveal]"));
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
    }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMobileNavOpen(false);
      mobileTriggerRef.current?.focus();
    };
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!mobileNavRef.current?.contains(target) && !mobileTriggerRef.current?.contains(target)) {
        setMobileNavOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOutside);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOutside);
    };
  }, [mobileNavOpen]);

  function navigate(action: () => void) {
    setMobileNavOpen(false);
    action();
  }

  function updateField<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [field]: value }));
    requestKeyRef.current = "";
    if (submitState !== "idle") {
      setSubmitState("idle");
      setSubmitMessage("");
      setInquiryId("");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form.privacyAccepted) {
      setSubmitState("error");
      setSubmitMessage("请先确认合作沟通所需的信息处理说明。");
      return;
    }
    setSubmitState("submitting");
    setSubmitMessage("");
    requestKeyRef.current ||= createRequestKey();
    try {
      const response = await submitCollaborationInquiry({
        collaborationType: form.collaborationType,
        name: form.name,
        organization: form.organization,
        contact: form.contact,
        message: form.message,
        website: form.website,
        privacyAccepted: form.privacyAccepted,
      }, requestKeyRef.current);
      setInquiryId(response.inquiryId);
      setSubmitState("success");
      setSubmitMessage("合作意向已收到。我们会先阅读你提供的背景，再通过预留方式联系你。");
    } catch (error) {
      setSubmitState("error");
      setSubmitMessage(error instanceof Error ? error.message : "暂时无法提交，请稍后再试。");
    }
  }

  return (
    <div ref={pageRef} className="about-site home-v3 h-dvh min-h-0 w-full min-w-0 overflow-x-clip overflow-y-auto overscroll-y-contain">
      <header className="home-header about-header">
        <a className="home-brand-link" href="/" aria-label="返回慧鉴AI官网首页" onClick={(event) => { event.preventDefault(); onHome(); }}>
          <HuijianBrand />
        </a>

        <nav className="home-nav home-desktop-nav" aria-label="关于与合作导航">
          <a href="/" onClick={(event) => { event.preventDefault(); onHome(); }}>产品首页</a>
          <a href="/?playground=1" onClick={(event) => { event.preventDefault(); onPlayground(); }}><Sparkles size={14} /> Playground</a>
          <a href="/?developer=1" onClick={(event) => { event.preventDefault(); onDeveloper(); }}>开发者平台</a>
          <a className="about-nav-current" href="#about">关于我们</a>
          <a href="#cooperation">开放合作</a>
        </nav>

        <div className="home-header-actions">
          <button ref={mobileTriggerRef} type="button" className="home-mobile-menu-button" onClick={() => setMobileNavOpen((value) => !value)} aria-label={mobileNavOpen ? "关闭网站导航" : "打开网站导航"} aria-expanded={mobileNavOpen} aria-controls="about-mobile-navigation">
            {mobileNavOpen ? <X size={19} /> : <Menu size={19} />}
          </button>
          {authReady && (user ? (
            <AccountMenu user={user} onWorkspace={onWorkspace} onDeveloper={onDeveloper} onLogout={onLogout} />
          ) : (
            <button type="button" className="home-login-button" onClick={onLogin} aria-label="登录账号"><LogIn size={17} /><span>登录</span></button>
          ))}
          <button type="button" className="home-workspace-button" onClick={onWorkspace}><span className="home-label-wide">开始鉴伪</span><span className="home-label-compact">鉴伪</span><ArrowRight size={17} /></button>
        </div>

        <Presence
          present={mobileNavOpen}
          onEnterComplete={() => mobileNavRef.current?.querySelector<HTMLElement>("a[href], button:not([disabled])")?.focus()}
        >
          {(phase) => (
            <nav ref={mobileNavRef} id="about-mobile-navigation" className="home-mobile-nav" aria-label="移动端关于与合作导航" aria-hidden={!mobileNavOpen} data-presence={phase}>
              <a href="/" tabIndex={mobileNavOpen ? 0 : -1} onClick={(event) => { event.preventDefault(); navigate(onHome); }}>产品首页<ArrowRight size={16} /></a>
              <button type="button" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => navigate(onPlayground)}>Playground<ArrowRight size={16} /></button>
              <button type="button" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => navigate(onDeveloper)}>开发者平台<ArrowRight size={16} /></button>
              <a href="#about" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => setMobileNavOpen(false)}>关于我们<ArrowRight size={16} /></a>
              <a href="#cooperation" tabIndex={mobileNavOpen ? 0 : -1} onClick={() => setMobileNavOpen(false)}>开放合作<ArrowRight size={16} /></a>
            </nav>
          )}
        </Presence>
      </header>

      <main>
        <section className="about-hero" id="about" aria-labelledby="about-title">
          <div className="about-hero-copy" data-about-reveal>
            <p className="about-kicker"><i /> ABOUT HUIJIAN AI</p>
            <h1 id="about-title" tabIndex={-1}>让真假判断，<br /><em>经得起追问。</em></h1>
            <p className="about-lead">慧鉴AI 是一个围绕 AI 生成内容真实性核验持续建设的项目。我们不只给出一个分数，而是把水印、元数据、模型判断与来源线索整理成普通人也能复核的证据。</p>
            <div className="about-hero-actions">
              <a href="#cooperation">聊聊合作 <ArrowRight size={18} /></a>
              <button type="button" onClick={onWorkspace}>体验慧鉴AI</button>
            </div>
            <ul aria-label="慧鉴AI能力范围">
              <li><span>01</span> 图片 · 视频 · 文档</li>
              <li><span>02</span> 快速检测 · Swarm 复核</li>
              <li><span>03</span> API · Agent Skill</li>
            </ul>
          </div>

          <figure className="about-hero-visual" data-about-reveal>
            <div className="about-portrait-stage">
              <span className="about-orbit about-orbit-one" />
              <span className="about-orbit about-orbit-two" />
              <img src="/brand/huijian-agent-portrait-gpt.webp" width="256" height="256" alt="慧鉴AI形象小鉴，手持放大镜观察数字内容" />
              <span className="about-evidence-tag tag-explain"><ShieldCheck size={17} /> 证据可解释</span>
              <span className="about-evidence-tag tag-review"><CheckCircle2 size={17} /> 结果可复核</span>
            </div>
            <figcaption><strong>小鉴</strong><span>慧鉴AI 的内容核验伙伴</span></figcaption>
          </figure>
        </section>

        <section className="about-statement" aria-label="慧鉴AI项目理念">
          <p data-about-reveal>我们相信，可信不是一句“相信模型”，而是让人看见模型依据、证据冲突与能力边界。</p>
          <div data-about-reveal>
            <span>我们的方向</span>
            <strong>让内容真实性判断从黑盒分数，变成可理解、可复查、可继续追问的过程。</strong>
          </div>
        </section>

        <section className="about-collaboration" aria-labelledby="collaboration-title">
          <div className="about-section-heading" data-about-reveal>
            <p><i /> OPEN COLLABORATION</p>
            <h2 id="collaboration-title">我们希望和认真解决问题的人，一起往前走。</h2>
            <span>无论你带来的是研究问题、真实数据、业务场景还是产品能力，都可以先从一次小范围验证开始。</span>
          </div>
          <div className="about-collaboration-grid">
            {COLLABORATION_AREAS.map((area, index) => {
              const Icon = area.icon;
              return (
                <article key={area.title} data-about-reveal>
                  <header><span>{String(index + 1).padStart(2, "0")}</span><Icon size={25} /></header>
                  <p>{area.audience}</p>
                  <h3>{area.title}</h3>
                  <div>{area.description}</div>
                  <a href="#cooperation" aria-label={`就${area.title}发起合作沟通`}>发起沟通 <ArrowRight size={17} /></a>
                </article>
              );
            })}
          </div>
        </section>

        <section className="about-principles" aria-labelledby="principles-title">
          <div data-about-reveal>
            <p>WORKING PRINCIPLES</p>
            <h2 id="principles-title">先验证价值，<br />再扩大合作。</h2>
          </div>
          <ul>
            <li data-about-reveal><LockKeyhole size={23} /><strong>数据有边界</strong><span>明确授权、用途和保留范围，不以合作为由扩大数据使用。</span></li>
            <li data-about-reveal><FlaskConical size={23} /><strong>结果可复现</strong><span>优先约定样本、指标和基线，用同一套标准讨论效果。</span></li>
            <li data-about-reveal><Handshake size={23} /><strong>能力说实话</strong><span>同时呈现有效结果、失败案例和不确定性，不承诺模型做不到的事。</span></li>
          </ul>
        </section>

        <section className="about-process" aria-labelledby="process-title">
          <div className="about-section-heading" data-about-reveal>
            <p><i /> HOW WE START</p>
            <h2 id="process-title">一次合作，从三件小事开始。</h2>
          </div>
          <ol>
            {PROCESS.map((step) => (
              <li key={step.number} data-about-reveal><span>{step.number}</span><div><strong>{step.title}</strong><p>{step.copy}</p></div></li>
            ))}
          </ol>
        </section>

        <section className="about-contact" id="cooperation" aria-labelledby="contact-title">
          <div className="about-contact-copy" data-about-reveal>
            <p><i /> START A CONVERSATION</p>
            <h2 id="contact-title">把你正在解决的问题，讲给我们听。</h2>
            <span>不需要先写一份正式方案。场景、样本、目标和目前遇到的困难，往往比漂亮的介绍更有帮助。</span>
            <div><ShieldCheck size={20} /><p><strong>信息用途</strong><small>信息仅用于评估与回复本次合作意向，不会作为公开案例或训练数据；未进入合作的意向默认保存不超过 365 天。</small></p></div>
          </div>

          <form className="about-contact-form" onSubmit={submit} data-about-reveal>
            {submitState === "success" ? (
              <div className="about-submit-success" role="status">
                <CheckCircle2 size={34} />
                <p>合作意向已提交</p>
                <span>{submitMessage}</span>
                {inquiryId && <small>参考编号：{inquiryId}</small>}
                <button type="button" onClick={() => { setForm(INITIAL_FORM); setSubmitState("idle"); setSubmitMessage(""); setInquiryId(""); requestKeyRef.current = ""; }}>再提交一项合作</button>
              </div>
            ) : (
              <>
                <div className="about-form-heading"><span>合作意向</span><small>预计填写 2 分钟</small></div>
                <label>
                  <span>希望合作的方向</span>
                  <select value={form.collaborationType} onChange={(event) => updateField("collaborationType", event.target.value as CollaborationInquiryInput["collaborationType"])}>
                    <option value="research">联合研究与科学评测</option>
                    <option value="governance">内容真实性治理</option>
                    <option value="dataset">数据与基准共建</option>
                    <option value="integration">产品与能力接入</option>
                    <option value="other">其他合作</option>
                  </select>
                </label>
                <div className="about-form-row">
                  <label><span>怎么称呼你</span><input value={form.name} onChange={(event) => updateField("name", event.target.value)} maxLength={60} autoComplete="name" required placeholder="姓名或称呼" /></label>
                  <label><span>所在机构 <small>选填</small></span><input value={form.organization} onChange={(event) => updateField("organization", event.target.value)} maxLength={120} autoComplete="organization" placeholder="学校、团队或公司" /></label>
                </div>
                <label><span>联系方式</span><input value={form.contact} onChange={(event) => updateField("contact", event.target.value)} minLength={4} maxLength={160} autoComplete="email" required placeholder="邮箱、手机号或微信号" /></label>
                <label><span>你想一起解决什么问题</span><textarea value={form.message} onChange={(event) => updateField("message", event.target.value)} minLength={20} maxLength={2000} required rows={6} placeholder="例如：使用场景、数据规模、希望验证的目标，以及目前最棘手的问题。" /></label>
                <label className="about-honeypot" aria-hidden="true"><span>网站</span><input value={form.website} onChange={(event) => updateField("website", event.target.value)} tabIndex={-1} autoComplete="off" /></label>
                <label className="about-privacy-check"><input type="checkbox" checked={form.privacyAccepted} onChange={(event) => updateField("privacyAccepted", event.target.checked)} /><span>我同意仅为合作沟通而处理上述信息，并已阅读<a href="/legal/privacy.html" target="_blank" rel="noreferrer">隐私政策</a>。</span></label>
                {submitMessage && <p className="about-form-message" role="alert">{submitMessage}</p>}
                <button className="about-submit-button" type="submit" disabled={submitState === "submitting"}>
                  {submitState === "submitting" ? <><span className="about-submit-spinner" /> 正在提交</> : <>提交合作意向 <Send size={18} /></>}
                </button>
              </>
            )}
          </form>
        </section>
      </main>

      <footer className="home-footer about-footer">
        <HuijianBrand compact />
        <p>慧鉴AI 提供数字内容鉴伪辅助分析，不替代专业机构与人工最终判断。</p>
        <div>
          <a href="/" onClick={(event) => { event.preventDefault(); onHome(); }}>产品首页</a>
          <a href="#cooperation">开放合作</a>
          <a href="/legal/terms.html" target="_blank" rel="noreferrer">用户协议</a>
          <a href="/legal/privacy.html" target="_blank" rel="noreferrer">隐私政策</a>
          <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">浙ICP备2026051442号</a>
        </div>
      </footer>
    </div>
  );
}
