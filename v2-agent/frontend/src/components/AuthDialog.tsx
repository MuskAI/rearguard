import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, ChevronDown, Eye, EyeOff, KeyRound, LoaderCircle, LockKeyhole, MessageSquareText, ShieldCheck, Smartphone, UserRound, X } from "lucide-react";
import {
  AccountUser,
  ApiRequestError,
  completeSmsPasswordSetup,
  loginByPassword,
  loginBySms,
  registerAccount,
  resetAccountPassword,
  sendSmsCode,
} from "../api";
import HuijianBrand from "./HuijianBrand";

interface Props {
  open: boolean;
  onClose: () => void;
  onAuthenticated: (user: AccountUser) => void;
}

type Panel = "login" | "register" | "reset" | "setup";
type LoginMode = "password" | "sms";
type MessageTone = "error" | "info" | "success";

export default function AuthDialog({ open, onClose, onAuthenticated }: Props) {
  const [panel, setPanel] = useState<Panel>("login");
  const [loginMode, setLoginMode] = useState<LoginMode>("password");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [passwordConfirmVisible, setPasswordConfirmVisible] = useState(false);
  const [username, setUsername] = useState("");
  const [code, setCode] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<MessageTone>("info");
  const [passwordTouched, setPasswordTouched] = useState(false);
  const [passwordConfirmTouched, setPasswordConfirmTouched] = useState(false);
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [smsRequested, setSmsRequested] = useState(false);
  const [registeredHint, setRegisteredHint] = useState(false);
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
  const existingAccountPrompt = panel === "register" && registeredHint;
  const activeRegistration = panel === "register" && !registeredHint;
  const needsCode = activeRegistration || panel === "reset" || (panel === "login" && loginMode === "sms");
  const needsPassword = activeRegistration || panel === "reset" || panel === "setup" || (panel === "login" && loginMode === "password");
  const needsPasswordConfirm = activeRegistration || panel === "reset" || panel === "setup";
  const enforcesPasswordPolicy = activeRegistration || panel === "reset" || panel === "setup";
  const needsConsent = panel === "login" || activeRegistration;
  const passwordRules = [
    { label: "8 至 128 位", met: password.length >= 8 && password.length <= 128 },
    { label: "包含字母", met: /[A-Za-z]/.test(password) },
    { label: "包含数字", met: /\d/.test(password) },
  ];
  const passwordPolicyValid = passwordRules.every((rule) => rule.met);
  const passwordError = !password
    ? "请输入密码"
    : password.length < 8
      ? "密码还不到 8 位"
      : password.length > 128
        ? "密码不能超过 128 位"
        : !/[A-Za-z]/.test(password)
          ? "密码中还需要至少一个字母"
          : !/\d/.test(password)
            ? "密码中还需要至少一个数字"
            : "";
  const passwordConfirmError = !passwordConfirm
    ? "请再次输入密码"
    : password !== passwordConfirm
      ? "两次输入的密码不一致"
      : "";
  const showPasswordError = needsPassword
    && (passwordTouched || submitAttempted)
    && (!password || (enforcesPasswordPolicy && !passwordPolicyValid));
  const showPasswordConfirmError = needsPasswordConfirm
    && (passwordConfirmTouched || submitAttempted)
    && Boolean(passwordConfirmError);
  const maskedPhone = phone.replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2");

  function showMessage(text: string, tone: MessageTone = "info") {
    setMessage(text);
    setMessageTone(tone);
  }

  async function requestCode() {
    if (!validPhone) {
      showMessage("请先输入正确的 11 位手机号", "error");
      return;
    }
    if (sending || countdown > 0) return;
    setSending(true);
    setMessage("");
    setSmsRequested(false);
    setRegisteredHint(false);
    try {
      const scene = panel === "register" ? "register" : panel === "reset" ? "reset" : "login";
      const response = await sendSmsCode(phone, scene);
      if (!response.success) throw new Error(response.message || "验证码发送失败");
      setCountdown(Math.max(1, Math.min(response.resend_in || 60, 300)));
      setSmsRequested(true);
      showMessage(
        response.debug_code
          ? `本地验证码：${response.debug_code}`
          : response.message || "验证码请求已提交，通常会在 1 分钟内送达",
        response.delivery_status === "conditional" ? "info" : "success",
      );
    } catch (error) {
      if (error instanceof ApiRequestError && error.code === "account_exists") {
        setRegisteredHint(true);
        setMessage("");
        return;
      }
      if (error instanceof ApiRequestError && error.retryAfterMs > 0) {
        setCountdown(Math.max(1, Math.ceil(error.retryAfterMs / 1000)));
      }
      showMessage(error instanceof Error ? error.message : "验证码发送失败", "error");
    } finally {
      setSending(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitAttempted(true);
    if (needsConsent && !accepted) {
      showMessage("请先阅读并同意用户协议和隐私政策", "error");
      return;
    }
    if (panel !== "setup" && !validPhone) {
      showMessage("请输入正确的 11 位手机号", "error");
      return;
    }
    if (panel === "register" && !username.trim()) {
      showMessage("请输入昵称", "error");
      return;
    }
    if (needsPassword && !password) {
      showMessage("请输入密码", "error");
      return;
    }
    if (enforcesPasswordPolicy && !passwordPolicyValid) {
      showMessage(passwordError, "error");
      return;
    }
    if (needsPasswordConfirm && password !== passwordConfirm) {
      showMessage(passwordConfirmError, "error");
      return;
    }
    if (needsCode && !/^\d{4,8}$/.test(code)) {
      showMessage("请输入短信中的验证码", "error");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      if (panel === "register") {
        await registerAccount({
          phone,
          secret: password,
          secretConfirm: passwordConfirm,
          username,
          smsCode: code,
          acceptedTerms: accepted,
        });
        setPanel("login");
        setLoginMode("password");
        setCode("");
        setPasswordConfirm("");
        setSubmitAttempted(false);
        setPasswordTouched(false);
        setPasswordConfirmTouched(false);
        showMessage("注册成功，请使用刚才设置的密码登录", "success");
      } else if (panel === "reset") {
        await resetAccountPassword({
          phone,
          secret: password,
          secretConfirm: passwordConfirm,
          smsCode: code,
        });
        switchPanel("login");
        setLoginMode("password");
        showMessage("密码已修改，请使用新密码登录。", "success");
      } else if (panel === "setup") {
        const response = await completeSmsPasswordSetup(password, passwordConfirm);
        onAuthenticated(response.user);
      } else {
        const response = loginMode === "password"
          ? await loginByPassword(phone, password, accepted)
          : await loginBySms(phone, code, accepted);
        if ("requiresPasswordSetup" in response && response.requiresPasswordSetup) {
          setPanel("setup");
          setPassword("");
          setPasswordConfirm("");
          setCode("");
          return;
        }
        if (!response.user) throw new Error("登录状态返回异常，请重试");
        onAuthenticated(response.user);
      }
    } catch (error) {
      showMessage(
        error instanceof Error
          ? error.message
          : panel === "register"
            ? "注册失败"
            : panel === "reset"
              ? "密码重置失败"
            : panel === "setup"
              ? "密码设置失败"
              : "登录失败",
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  function switchPanel(next: Panel) {
    setPanel(next);
    setMessage("");
    setCode("");
    setPassword("");
    setPasswordConfirm("");
    setPasswordVisible(false);
    setPasswordConfirmVisible(false);
    setPasswordTouched(false);
    setPasswordConfirmTouched(false);
    setSubmitAttempted(false);
    setSmsRequested(false);
    setRegisteredHint(false);
  }

  function switchToSmsLogin() {
    switchPanel("login");
    setLoginMode("sms");
    showMessage("该手机号已注册，获取登录验证码后即可继续。", "info");
  }

  function switchToPasswordReset() {
    switchPanel("reset");
    showMessage("验证手机号后即可设置新密码。", "info");
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
        <div className="auth-brand-row">
          <HuijianBrand />
          <span className="auth-security-badge"><ShieldCheck size={16} /> 账号保护</span>
        </div>
        <div className="auth-heading">
          <h2 id="auth-title">
            {panel === "login" ? "欢迎回来" : existingAccountPrompt ? "这个手机号已有账号" : panel === "register" ? "创建慧鉴AI账号" : panel === "reset" ? "重置登录密码" : "设置登录密码"}
          </h2>
          <p id="auth-description">
            {panel === "login"
              ? "登录后继续查看你的任务与报告。"
              : existingAccountPrompt
                ? "无需重复注册，选择适合你的登录方式。"
              : panel === "register"
                ? "注册后即可保存个人鉴伪记录。"
                : panel === "reset"
                  ? "接收短信验证码，并为账号设置一个新密码。"
                  : "手机号验证成功，设置密码后即可进入慧鉴AI。"}
          </p>
        </div>

        {panel !== "setup" && panel !== "reset" && <div className="segmented auth-panels" role="tablist" aria-label="登录或注册" onKeyDown={movePanelFocus}>
          <button id="auth-tab-login" type="button" role="tab" aria-selected={panel === "login"} aria-controls="auth-panel" tabIndex={panel === "login" ? 0 : -1} className={panel === "login" ? "active" : ""} onClick={() => switchPanel("login")}>登录</button>
          <button id="auth-tab-register" type="button" role="tab" aria-selected={panel === "register"} aria-controls="auth-panel" tabIndex={panel === "register" ? 0 : -1} className={panel === "register" ? "active" : ""} onClick={() => switchPanel("register")}>注册</button>
        </div>}

        <div id="auth-panel" role="tabpanel" aria-labelledby={panel === "setup" || panel === "reset" ? "auth-title" : `auth-tab-${panel}`} tabIndex={0}>
          {panel === "reset" && (
            <button className="auth-recovery-back" type="button" onClick={() => switchPanel("login")}>
              <ArrowLeft size={17} /> 返回登录
            </button>
          )}
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

          <form className="auth-form" onSubmit={submit} noValidate>
          {activeRegistration && (
            <label>
              <span>昵称</span>
              <div className="field-shell"><UserRound size={17} /><input value={username} onChange={(event) => setUsername(event.target.value)} maxLength={128} placeholder="怎么称呼你" required /></div>
            </label>
          )}
          {panel === "setup" ? (
            <div className="auth-verified-phone" role="status">
              <span><Check size={15} /> 手机号已验证</span>
              <strong>+86 {maskedPhone}</strong>
              <button type="button" onClick={() => switchPanel("login")}>更换手机号</button>
            </div>
          ) : <label>
            <span>手机号</span>
            <div className="field-shell"><Smartphone size={17} /><span className="country-code">+86</span><input inputMode="numeric" autoComplete="tel" value={phone} onChange={(event) => { setPhone(event.target.value.replace(/\D/g, "").slice(0, 11)); setRegisteredHint(false); }} placeholder="请输入手机号" required /></div>
          </label>}
          {needsPassword && (
            <label className={showPasswordError ? "has-error" : ""}>
              <span>{panel === "setup" ? "设置密码" : panel === "reset" ? "新密码" : "密码"}</span>
              <div className="field-shell"><KeyRound size={18} /><input type={passwordVisible ? "text" : "password"} autoComplete={panel === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} onBlur={() => setPasswordTouched(true)} placeholder={enforcesPasswordPolicy ? "至少 8 位，包含字母和数字" : "请输入密码"} required minLength={enforcesPasswordPolicy ? 8 : undefined} maxLength={128} aria-invalid={showPasswordError} aria-describedby={enforcesPasswordPolicy ? `password-requirements${showPasswordError ? " password-error" : ""}` : showPasswordError ? "password-error" : undefined} /><button className="password-visibility" type="button" onClick={() => setPasswordVisible((visible) => !visible)} aria-label={passwordVisible ? "隐藏密码" : "显示密码"} title={passwordVisible ? "隐藏密码" : "显示密码"}>{passwordVisible ? <EyeOff size={18} /> : <Eye size={18} />}</button></div>
              {enforcesPasswordPolicy && (
                <div id="password-requirements" className="password-requirements" aria-label="密码要求">
                  {passwordRules.map((rule) => <span key={rule.label} className={rule.met ? "met" : ""}><Check size={12} />{rule.label}</span>)}
                </div>
              )}
              {showPasswordError && <p id="password-error" className="auth-field-error" role="alert">{passwordError}</p>}
            </label>
          )}
          {panel === "login" && loginMode === "password" && (
            <div className="auth-forgot-row">
              <button type="button" onClick={switchToPasswordReset}>忘记密码？修改密码</button>
            </div>
          )}
          {needsPasswordConfirm && (
            <label className={showPasswordConfirmError ? "has-error" : ""}>
              <span>确认密码</span>
              <div className="field-shell"><KeyRound size={18} /><input type={passwordConfirmVisible ? "text" : "password"} autoComplete="new-password" value={passwordConfirm} onChange={(event) => setPasswordConfirm(event.target.value)} onBlur={() => setPasswordConfirmTouched(true)} placeholder="请再次输入相同密码" required minLength={8} maxLength={128} aria-invalid={showPasswordConfirmError} aria-describedby={showPasswordConfirmError ? "password-confirm-error" : undefined} /><button className="password-visibility" type="button" onClick={() => setPasswordConfirmVisible((visible) => !visible)} aria-label={passwordConfirmVisible ? "隐藏确认密码" : "显示确认密码"} title={passwordConfirmVisible ? "隐藏确认密码" : "显示确认密码"}>{passwordConfirmVisible ? <EyeOff size={18} /> : <Eye size={18} />}</button></div>
              {showPasswordConfirmError && <p id="password-confirm-error" className="auth-field-error" role="alert">{passwordConfirmError}</p>}
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
              {smsRequested && <p className="sms-delivery-help">还没收到？请检查短信拦截与手机号；倒计时结束后可以重新发送。</p>}
            </label>
          )}

          {existingAccountPrompt && (
            <section className="auth-account-guidance" role="status" aria-label="该手机号已注册">
              <div>
                <ShieldCheck size={19} />
                <span><strong>这个手机号已经注册</strong><small>无需重复创建账号，请选择下一步。</small></span>
              </div>
              <div className="auth-account-guidance-actions">
                <button type="button" onClick={switchToSmsLogin}><MessageSquareText size={16} /> 验证码登录</button>
                <button type="button" onClick={switchToPasswordReset}><KeyRound size={16} /> 忘记密码</button>
              </div>
            </section>
          )}

          {needsConsent && (
            <section className="auth-consent-brief" aria-labelledby="auth-consent-title">
              <details className="auth-privacy-details">
                <summary><span><LockKeyhole size={17} /><strong id="auth-consent-title">账号与数据保护</strong></span><ChevronDown size={18} /></summary>
                <div className="auth-privacy-copy">
                  <p>手机号仅用于登录与账号安全；上传内容用于鉴伪、报告与个人历史。</p>
                  <p>任务与报告按账号隔离，你可以随时删除个人历史或撤销分享。</p>
                </div>
              </details>
              <label className="terms-check">
                <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
                <span className="check-visual"><Check size={13} /></span>
                <span>我已阅读并同意 <a href="/legal/terms.html" target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>用户协议</a> 与 <a href="/legal/privacy.html" target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>隐私政策</a></span>
              </label>
            </section>
          )}

          {message && <div className={`auth-message ${messageTone}`} role={messageTone === "error" ? "alert" : "status"}>{message}</div>}
          {!existingAccountPrompt && (
            <button className="primary-button auth-submit" type="submit" disabled={busy || !validPhone || (needsConsent && !accepted)}>
              {busy && <LoaderCircle size={17} className="spin" />}
              {busy ? "处理中" : panel === "login" ? "安全登录" : panel === "register" ? "创建账号" : panel === "reset" ? "确认修改密码" : "设置密码并登录"}
            </button>
          )}
          </form>
        </div>
      </section>
    </div>
  );
}
