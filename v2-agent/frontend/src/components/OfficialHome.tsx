import {
  ArrowRight,
  BadgeCheck,
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
  onDeveloper: () => void;
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

export default function OfficialHome({ authReady, health, user, onEnterWorkspace, onDeveloper, onLogin }: Props) {
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
          <button type="button" onClick={onDeveloper}>开发者平台</button>
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
            开始鉴伪 <ArrowRight size={17} />
          </button>
        </div>
      </header>

      <main>
        <section className="official-hero" id="home" aria-labelledby="official-home-title">
          <div className="official-hero-scene" aria-hidden="true">
            <div className="official-scan-frame">
              <span className="scan-corner corner-a" />
              <span className="scan-corner corner-b" />
              <span className="scan-corner corner-c" />
              <span className="scan-corner corner-d" />
              <div className="official-mascot-halo" />
              <img src="/brand/huijian-mascot.webp" alt="" width="594" height="800" />
            </div>
          </div>

          <div className="official-hero-inner">
            <div className="official-hero-copy">
              <p className="official-eyebrow"><Sparkles size={16} /> 数字内容鉴伪智能体</p>
              <h1 id="official-home-title"><span>慧鉴AI</span>让判断有证据可循</h1>
              <p className="official-hero-description">
                汇集模型判断、来源核验与内容证据，给出可理解、可追溯的辅助结论。
              </p>
              <div className="official-hero-actions">
                <button type="button" className="official-primary-cta" onClick={onEnterWorkspace}>
                  开始鉴伪 <ArrowRight size={19} />
                </button>
                <a className="official-secondary-cta" href="#capabilities">了解产品能力 <ChevronDown size={18} /></a>
              </div>
            </div>
          </div>
        </section>

        <section className="official-proof-rail" aria-label="慧鉴AI核心原则">
          <article><ShieldCheck size={21} /><div><strong>真实服务链路</strong><span>异常时明确提示</span></div></article>
          <article><Waypoints size={21} /><div><strong>多源证据组织</strong><span>结论与依据同步呈现</span></div></article>
          <article><LockKeyhole size={21} /><div><strong>个人任务隔离</strong><span>历史记录按账户访问</span></div></article>
        </section>

        <section className="official-section capabilities-section" id="capabilities" aria-labelledby="capabilities-title">
          <div className="official-section-heading">
            <h2 id="capabilities-title">不止回答真假，<br />还要讲清楚为什么。</h2>
            <p>慧鉴AI 沿着模型、来源与内容三个方向整理证据，让每个结论都更适合复核与沟通。</p>
          </div>
          <div className="capability-mosaic">
            <figure className="capability-visual">
              <img
                src="/brand/huijian-evidence-studio.webp"
                alt="小鉴正在核验图像、文档与视频内容"
                width="1536"
                height="1024"
                loading="lazy"
              />
            </figure>
            <article className="capability-model">
              <div className="capability-icon"><ScanSearch size={24} /></div>
              <h3>模型痕迹分析</h3>
              <p>发现统计异常、局部纹理与生成模型特征，呈现可复核的风险线索。</p>
              <small><ImageIcon size={14} /> 图像检测</small>
            </article>
            <article className="capability-origin">
              <div className="capability-icon"><Fingerprint size={24} /></div>
              <h3>来源与凭证核验</h3>
              <p>结合元数据、内容凭证与可用来源线索，为模型判断补充必要上下文。</p>
              <small><FileCheck2 size={14} /> 来源证据</small>
            </article>
            <article className="capability-multimodal">
              <div className="capability-icon"><Layers3 size={24} /></div>
              <h3>多模态证据组织</h3>
              <p>图像、视频与文档进入同一条分析链路，不再分散在不同版本与入口。</p>
              <small><Video size={14} /> 统一入口</small>
            </article>
            <article className="capability-report">
              <div className="capability-icon"><FileText size={24} /></div>
              <h3>报告与任务归档</h3>
              <p>保留关键依据、处理信息与结论，便于后续复核、下载与留档。</p>
              <small><FileText size={14} /> 可追溯报告</small>
            </article>
          </div>
        </section>

        <section className="workflow-section" id="workflow" aria-labelledby="official-workflow-title">
          <div className="official-section workflow-inner">
            <div className="workflow-intro">
              <h2 id="official-workflow-title">提交一份内容，<br />沿着证据链走完。</h2>
              <p>系统根据文件类型与当前可用能力完成调度，并把每一步保留在同一份任务中。</p>
            </div>
            <ol className="official-workflow-list">
              <li><Waypoints size={24} /><div><strong>识别任务</strong><p>确认内容类型、文件信息与分析条件。</p></div></li>
              <li><Layers3 size={24} /><div><strong>调度能力</strong><p>选择当前可用的模型与证据核验链路。</p></div></li>
              <li><ScanSearch size={24} /><div><strong>交叉核验</strong><p>综合模型信号、来源线索与内容结构。</p></div></li>
              <li><FileCheck2 size={24} /><div><strong>形成报告</strong><p>呈现结论、依据与必要的不确定性。</p></div></li>
            </ol>
          </div>
        </section>

        <section className="official-section trust-section" id="trust" aria-labelledby="trust-title">
          <div className="trust-heading">
            <h2 id="trust-title">可信的系统，<br />先把边界讲清楚。</h2>
            <p>面对高风险内容，清楚说明能力范围与不确定性，比给出一个过度肯定的数字更重要。</p>
            <aside className="trust-mascot-note">
              <img src="/brand/huijian-mascot.webp" alt="慧鉴AI品牌助手小鉴" width="82" height="110" />
              <div><span>小鉴的原则</span><strong>证据不足时，诚实地说“不确定”。</strong></div>
            </aside>
          </div>
          <div className="trust-principles">
            <article><ShieldCheck size={25} /><div><h3>拒绝模拟结果</h3><p>检测服务不可用时明确提示，不用随机数字或伪造结论代替真实响应。</p></div></article>
            <article><LockKeyhole size={25} /><div><h3>账户数据隔离</h3><p>登录用户只能访问自己的任务与历史记录，减少敏感内容暴露风险。</p></div></article>
            <article><BadgeCheck size={25} /><div><h3>保留人工复核</h3><p>新闻、司法等高风险场景仍应结合原始来源与专业人员判断。</p></div></article>
          </div>
        </section>

        <section className="official-final-cta" aria-labelledby="final-cta-title">
          <div>
            <h2 id="final-cta-title">把第一份内容交给小鉴。</h2>
            <p>从上传到报告，在一个任务中看见判断背后的证据。</p>
          </div>
          <button type="button" onClick={onEnterWorkspace}>开始鉴伪 <ArrowRight size={20} /></button>
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
