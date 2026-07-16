import {
  ArrowRight,
  BadgeCheck,
  Check,
  ChevronDown,
  FileCheck2,
  FileText,
  Fingerprint,
  Image as ImageIcon,
  Layers3,
  LockKeyhole,
  LogIn,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  UserRound,
  Video,
  Waypoints,
} from "lucide-react";
import type { AccountUser, HealthStatus } from "../api";
import HuijianBrand from "./HuijianBrand";

interface Props {
  authReady: boolean;
  health: HealthStatus | null;
  user: AccountUser | null;
  onEnterWorkspace: () => void;
  onLogin: () => void;
}

function getServiceState(health: HealthStatus | null) {
  if (!health) return { tone: "checking", label: "正在检查服务" };
  const capabilityStates = Object.values(health.capabilities || {});
  const hasLimitedCapability = capabilityStates.some((state) => state !== "available");
  if (health.status === "ok" && health.vlmEnabled && !hasLimitedCapability) {
    return { tone: "online", label: "核心服务正常" };
  }
  return { tone: "limited", label: "部分能力受限" };
}

export default function OfficialHome({ authReady, health, user, onEnterWorkspace, onLogin }: Props) {
  const service = getServiceState(health);

  return (
    <div className="official-site">
      <header className="official-header">
        <a className="official-brand-link" href="#home" aria-label="返回慧鉴AI官网首页">
          <HuijianBrand />
        </a>
        <nav className="official-nav" aria-label="官网导航">
          <a href="#capabilities">产品能力</a>
          <a href="#workflow">工作方式</a>
          <a href="#trust">可信机制</a>
        </nav>
        <div className="official-header-actions">
          <span className={`official-service-state ${service.tone}`}><i />{service.label}</span>
          {authReady && (user ? (
            <button type="button" className="official-account-button" onClick={onEnterWorkspace} aria-label={`进入${user.username || "当前用户"}的工作台`}>
              <UserRound size={17} />
              <span>{user.username || "我的任务"}</span>
            </button>
          ) : (
            <button type="button" className="official-login-button" onClick={onLogin} aria-label="登录慧鉴AI">
              <LogIn size={17} />
              <span>登录</span>
            </button>
          ))}
          <button type="button" className="official-workspace-button" onClick={onEnterWorkspace}>
            进入工作台 <ArrowRight size={17} />
          </button>
        </div>
      </header>

      <main>
        <section className="official-hero" id="home" aria-labelledby="official-home-title">
          <div className="official-hero-grid" aria-hidden="true" />
          <div className="official-hero-scene" aria-hidden="true">
            <div className="official-scan-frame">
              <span className="scan-corner corner-a" />
              <span className="scan-corner corner-b" />
              <span className="scan-corner corner-c" />
              <span className="scan-corner corner-d" />
              <div className="official-mascot-halo" />
              <img src="/brand/huijian-mascot.webp" alt="" width="594" height="800" />
              <span className="evidence-node evidence-node-source"><Fingerprint size={18} /><b>来源线索</b><small>Origin</small></span>
              <span className="evidence-node evidence-node-model"><ScanSearch size={18} /><b>模型痕迹</b><small>Signal</small></span>
              <span className="evidence-node evidence-node-structure"><Layers3 size={18} /><b>内容结构</b><small>Structure</small></span>
              <span className="evidence-connection connection-a" />
              <span className="evidence-connection connection-b" />
              <span className="evidence-connection connection-c" />
            </div>
          </div>

          <div className="official-hero-inner">
            <div className="official-hero-copy">
              <p className="official-eyebrow"><Sparkles size={16} /> 数字内容鉴伪智能体</p>
              <h1 id="official-home-title">慧鉴AI</h1>
              <p className="official-hero-statement">让每一份判断，<br />都有证据可循。</p>
              <p className="official-hero-description">
                面向图像、视频与文档的内容鉴伪平台。慧鉴AI 组织模型判断、来源核验与内容证据，形成可理解、可追溯的辅助结论。
              </p>
              <div className="official-hero-actions">
                <button type="button" className="official-primary-cta" onClick={onEnterWorkspace}>
                  进入鉴伪工作台 <ArrowRight size={19} />
                </button>
                <a className="official-secondary-cta" href="#capabilities">了解产品能力 <ChevronDown size={18} /></a>
              </div>
              <p className="official-privacy-note"><LockKeyhole size={15} /> 登录后任务按账号隔离保存，游客也可先体验检测。</p>
            </div>
          </div>

          <div className="official-hero-ledger" aria-label="慧鉴AI核心原则">
            <span><b>01</b><strong>真实检测链路</strong><small>服务异常时明确提示</small></span>
            <span><b>02</b><strong>多源证据组织</strong><small>结论与依据同步呈现</small></span>
            <span><b>03</b><strong>个人任务隔离</strong><small>历史记录按账户访问</small></span>
          </div>
        </section>

        <section className="official-section capabilities-section" id="capabilities" aria-labelledby="capabilities-title">
          <div className="official-section-heading">
            <div><span>01 · PRODUCT</span><h2 id="capabilities-title">不止识别真假，<br />更重要的是说明依据。</h2></div>
            <p>慧鉴AI 不把判断压缩成一个孤立数字，而是沿着模型、来源和内容三个方向整理证据，让结果更适合复核与沟通。</p>
          </div>
          <div className="capability-ledger">
            <article>
              <span className="capability-number">01</span>
              <div className="capability-icon blue"><ScanSearch size={24} /></div>
              <h3>模型痕迹分析</h3>
              <p>发现生成内容中的统计异常、局部纹理和模型特征，并呈现风险信息。</p>
              <small><ImageIcon size={14} /> 图像内容</small>
            </article>
            <article>
              <span className="capability-number">02</span>
              <div className="capability-icon teal"><Fingerprint size={24} /></div>
              <h3>来源与凭证核验</h3>
              <p>结合元数据、内容凭证和可用来源线索，为模型判断补充上下文。</p>
              <small><FileCheck2 size={14} /> 来源证据</small>
            </article>
            <article>
              <span className="capability-number">03</span>
              <div className="capability-icon coral"><Layers3 size={24} /></div>
              <h3>多模态证据组织</h3>
              <p>统一承载图像、视频与文档任务，让不同能力在同一条分析链路中协作。</p>
              <small><Video size={14} /> 多类内容</small>
            </article>
            <article>
              <span className="capability-number">04</span>
              <div className="capability-icon amber"><FileText size={24} /></div>
              <h3>报告与任务归档</h3>
              <p>保留关键依据、处理信息与结论，便于后续复核、下载和留档。</p>
              <small><FileText size={14} /> 可追溯报告</small>
            </article>
          </div>
        </section>

        <section className="workflow-section" id="workflow" aria-labelledby="official-workflow-title">
          <div className="official-section workflow-inner">
            <div className="workflow-intro">
              <span>02 · WORKFLOW</span>
              <h2 id="official-workflow-title">把复杂的鉴伪能力，<br />组织成一条清晰路径。</h2>
              <p>用户只需提交内容，其余步骤由慧鉴AI 根据文件类型与当前可用能力完成调度。</p>
              <button type="button" onClick={onEnterWorkspace}>开始一次鉴伪 <ArrowRight size={18} /></button>
            </div>
            <ol className="official-workflow-list">
              <li><span>01</span><Waypoints size={22} /><div><strong>识别任务</strong><p>确认内容类型、文件信息和分析条件。</p></div></li>
              <li><span>02</span><Layers3 size={22} /><div><strong>调度能力</strong><p>选择当前可用的模型与证据核验链路。</p></div></li>
              <li><span>03</span><ScanSearch size={22} /><div><strong>交叉核验</strong><p>把模型信号、来源线索与内容结构放在一起分析。</p></div></li>
              <li><span>04</span><FileCheck2 size={22} /><div><strong>形成报告</strong><p>呈现结论、依据和必要的不确定性说明。</p></div></li>
            </ol>
          </div>
        </section>

        <section className="official-section trust-section" id="trust" aria-labelledby="trust-title">
          <div className="trust-heading">
            <span>03 · TRUST</span>
            <h2 id="trust-title">可信，不是说得更肯定。<br />而是把边界讲清楚。</h2>
          </div>
          <div className="trust-principles">
            <article><ShieldCheck size={23} /><div><h3>拒绝模拟结果</h3><p>检测服务不可用时明确提示，不使用随机数字或伪造结论代替真实响应。</p></div><Check size={18} /></article>
            <article><LockKeyhole size={23} /><div><h3>账户数据隔离</h3><p>登录用户只能访问自己的任务与历史记录，减少敏感内容的暴露风险。</p></div><Check size={18} /></article>
            <article><BadgeCheck size={23} /><div><h3>保留人工判断</h3><p>系统提供辅助证据；新闻、司法等高风险场景仍应结合原始来源与人工复核。</p></div><Check size={18} /></article>
          </div>
          <aside className="trust-mascot-note">
            <img src="/brand/huijian-mascot.webp" alt="慧鉴AI品牌助手小鉴" width="82" height="110" />
            <div><span>小鉴的原则</span><strong>证据不足时，诚实地说“不确定”。</strong></div>
          </aside>
        </section>

        <section className="official-final-cta" aria-labelledby="final-cta-title">
          <div>
            <span>READY TO VERIFY</span>
            <h2 id="final-cta-title">从一份内容开始，<br />看见判断背后的证据。</h2>
          </div>
          <button type="button" onClick={onEnterWorkspace}>进入慧鉴AI工作台 <ArrowRight size={20} /></button>
        </section>
      </main>

      <footer className="official-footer">
        <HuijianBrand compact />
        <p>慧鉴AI 提供数字内容鉴伪辅助分析，不替代专业机构与人工最终判断。</p>
        <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">浙ICP备2026051442号</a>
      </footer>
    </div>
  );
}
