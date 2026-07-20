import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";
import { Check, KeyRound, LoaderCircle, MessageSquareText, Smartphone, UserRound, X } from "lucide-react";
import {
  AccountUser,
  loginByPassword,
  loginBySms,
  registerAccount,
  sendSmsCode,
} from "../api";
import HuijianBrand from "./HuijianBrand";

interface Props {
  open: boolean;
  onClose: () => void;
  onAuthenticated: (user: AccountUser) => void;
}

type Panel = "login" | "register";
type LoginMode = "password" | "sms";

export default function AuthDialog({ open, onClose, onAuthenticated }: Props) {
  const [panel, setPanel] = useState<Panel>("login");
  const [loginMode, setLoginMode] = useState<LoginMode>("password");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [code, setCode] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [message, setMessage] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  const busyRef = useRef(busy);
  const onCloseRef = useRef(onClose);
  busyRef.current = busy;
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => {
      const target = dialogRef.current?.querySelector<HTMLElement>(".auth-form input:not([disabled])")
        || dialogRef.current?.querySelector<HTMLElement>("button:not([disabled])");
      target?.focus();
    }, 0);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) || []).filter((element) => !element.hasAttribute("hidden"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = window.setInterval(() => setCountdown((value) => Math.max(value - 1, 0)), 1000);
    return () => window.clearInterval(timer);
  }, [countdown]);

  if (!open) return null;

  const validPhone = /^1[3-9]\d{9}$/.test(phone);
  const needsCode = panel === "register" || loginMode === "sms";

  async function requestCode() {
    if (!validPhone || sending || countdown > 0) return;
    setSending(true);
    setMessage("");
    try {
      const response = await sendSmsCode(phone, panel === "register" ? "register" : "login");
      setCountdown(60);
      setMessage(response.debug_code ? `本地验证码：${response.debug_code}` : "验证码已发送，请留意短信");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "验证码发送失败");
    } finally {
      setSending(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!accepted) {
      setMessage("请先阅读并同意用户协议和隐私政策");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      if (panel === "register") {
        await registerAccount({ phone, secret: password, username, smsCode: code, acceptedTerms: accepted });
        setPanel("login");
        setLoginMode("password");
        setCode("");
        setMessage("注册成功，请使用刚才设置的密码登录");
      } else {
        const response = loginMode === "password"
          ? await loginByPassword(phone, password, accepted)
          : await loginBySms(phone, code, accepted);
        onAuthenticated(response.user);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : panel === "register" ? "注册失败" : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  function switchPanel(next: Panel) {
    setPanel(next);
    setMessage("");
    setCode("");
  }

  function movePanelFocus(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (current < 0) return;
    event.preventDefault();
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next]?.focus();
    tabs[next]?.click();
  }

  return (
    <div className="dialog-layer" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section ref={dialogRef} className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title" aria-describedby="auth-description">
        <button className="icon-button dialog-close" type="button" onClick={onClose} disabled={busy} aria-label="关闭登录窗口" title="关闭">
          <X size={18} />
        </button>
        <HuijianBrand />
        <div className="auth-heading">
          <h2 id="auth-title">{panel === "login" ? "欢迎回来" : "创建慧鉴AI账号"}</h2>
          <p id="auth-description">{panel === "login" ? "登录后，任务与报告只对你本人可见。" : "注册后即可保存个人鉴伪记录。"}</p>
        </div>

        <div className="segmented auth-panels" role="tablist" aria-label="登录或注册" onKeyDown={movePanelFocus}>
          <button id="auth-tab-login" type="button" role="tab" aria-selected={panel === "login"} aria-controls="auth-panel" tabIndex={panel === "login" ? 0 : -1} className={panel === "login" ? "active" : ""} onClick={() => switchPanel("login")}>登录</button>
          <button id="auth-tab-register" type="button" role="tab" aria-selected={panel === "register"} aria-controls="auth-panel" tabIndex={panel === "register" ? 0 : -1} className={panel === "register" ? "active" : ""} onClick={() => switchPanel("register")}>注册</button>
        </div>

        <div id="auth-panel" role="tabpanel" aria-labelledby={`auth-tab-${panel}`} tabIndex={0}>
          {panel === "login" && (
            <div className="auth-mode-switch" role="group" aria-label="登录方式">
              <button type="button" aria-pressed={loginMode === "password"} className={loginMode === "password" ? "active" : ""} onClick={() => { setLoginMode("password"); setMessage(""); }}>
                <KeyRound size={15} /> 密码登录
              </button>
              <button type="button" aria-pressed={loginMode === "sms"} className={loginMode === "sms" ? "active" : ""} onClick={() => { setLoginMode("sms"); setMessage(""); }}>
                <MessageSquareText size={15} /> 验证码登录
              </button>
            </div>
          )}

          <form className="auth-form" onSubmit={submit}>
          {panel === "register" && (
            <label>
              <span>昵称</span>
              <div className="field-shell"><UserRound size={17} /><input value={username} onChange={(event) => setUsername(event.target.value)} maxLength={128} placeholder="怎么称呼你" required /></div>
            </label>
          )}
          <label>
            <span>手机号</span>
            <div className="field-shell"><Smartphone size={17} /><span className="country-code">+86</span><input inputMode="numeric" autoComplete="tel" value={phone} onChange={(event) => setPhone(event.target.value.replace(/\D/g, "").slice(0, 11))} placeholder="请输入手机号" required /></div>
          </label>
          {(panel === "register" || loginMode === "password") && (
            <label>
              <span>密码</span>
              <div className="field-shell"><KeyRound size={17} /><input type="password" autoComplete={panel === "register" ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 8 位，包含字母和数字" required minLength={8} /></div>
            </label>
          )}
          {needsCode && (
            <label>
              <span>短信验证码</span>
              <div className="code-row">
                <div className="field-shell"><MessageSquareText size={17} /><input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 8))} placeholder="输入验证码" required /></div>
                <button className="secondary-button code-button" type="button" disabled={!validPhone || sending || countdown > 0} onClick={requestCode}>
                  {sending ? <LoaderCircle size={16} className="spin" /> : countdown > 0 ? `${countdown}s` : "获取验证码"}
                </button>
              </div>
            </label>
          )}

          <label className="terms-check">
            <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
            <span className="check-visual"><Check size={13} /></span>
            <span>我已阅读并同意 <a href="/legal/terms.html" target="_blank" rel="noreferrer">用户协议</a> 和 <a href="/legal/privacy.html" target="_blank" rel="noreferrer">隐私政策</a></span>
          </label>

          {message && <div className="auth-message" role="status">{message}</div>}
          <button className="primary-button auth-submit" type="submit" disabled={busy || !validPhone || !accepted}>
            {busy && <LoaderCircle size={17} className="spin" />}
            {busy ? "处理中" : panel === "login" ? "安全登录" : "创建账号"}
          </button>
          </form>
        </div>
      </section>
    </div>
  );
}
