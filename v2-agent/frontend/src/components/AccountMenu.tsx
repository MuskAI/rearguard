import {
  ChevronDown,
  Code2,
  History,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { KeyboardEvent as ReactKeyboardEvent, useEffect, useId, useRef, useState } from "react";
import type { AccountUser } from "../api";
import { UserAvatar } from "./BrandSystem";
import Presence from "./Presence";

interface Props {
  user: AccountUser;
  onWorkspace: () => void;
  onDeveloper: () => void;
  onLogout: () => void;
  compact?: boolean;
  className?: string;
}

function maskPhone(phone: string) {
  return phone.replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2");
}

export default function AccountMenu({ user, onWorkspace, onDeveloper, onLogout, compact = false, className = "" }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const pendingFocusRef = useRef<number | null>(null);
  const pendingActionRef = useRef<(() => void) | null>(null);
  const menuId = useId();
  const displayName = user.username || "慧鉴用户";
  const avatarSeed = user.account_uuid || String(user.Userid);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        pendingActionRef.current = null;
        setOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      pendingActionRef.current = null;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  function openWithFocus(index: number) {
    pendingActionRef.current = null;
    if (open) {
      itemRefs.current[index]?.focus();
      return;
    }
    pendingFocusRef.current = index;
    setOpen(true);
  }

  function handleItemKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowDown") nextIndex = (index + 1) % 3;
    if (event.key === "ArrowUp") nextIndex = (index - 1 + 3) % 3;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = 2;
    if (nextIndex === null) return;
    event.preventDefault();
    itemRefs.current[nextIndex]?.focus();
  }

  function run(action: () => void) {
    pendingActionRef.current = action;
    setOpen(false);
    triggerRef.current?.focus();
  }

  return (
    <div
      ref={rootRef}
      className={`account-menu ${compact ? "is-compact" : ""} ${open ? "is-open" : ""} ${className}`.trim()}
      onBlur={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        pendingActionRef.current = null;
        setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className="account-menu-trigger"
        aria-label={`${displayName}，查看账户信息`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => {
          pendingActionRef.current = null;
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          openWithFocus(event.key === "ArrowDown" ? 0 : 2);
        }}
      >
        <span className="account-menu-avatar"><UserAvatar seed={avatarSeed} displayName={displayName} size={32} className={compact ? "account-control-artwork" : ""} status="online" /></span>
        {!compact && <span>{displayName}</span>}
        {!compact && <ChevronDown size={14} aria-hidden="true" />}
      </button>
      <Presence
        present={open}
        onEnterComplete={() => {
          const focusIndex = pendingFocusRef.current ?? 0;
          pendingFocusRef.current = null;
          itemRefs.current[focusIndex]?.focus();
        }}
        onExitComplete={() => {
          const action = pendingActionRef.current;
          pendingActionRef.current = null;
          action?.();
        }}
      >
        {(phase) => (
          <div id={menuId} className="account-menu-popover" role="menu" aria-label="账户信息与操作" aria-hidden={!open} data-presence={phase}>
            <div className="account-menu-profile">
              <span className="account-menu-profile-avatar"><UserAvatar seed={avatarSeed} displayName={displayName} size={42} status="online" /></span>
              <div><strong>{displayName}</strong><small>{maskPhone(user.phone || "手机号未绑定")}</small></div>
              <span className="account-verified"><ShieldCheck size={13} /> 已登录</span>
            </div>
            <div className="account-menu-actions">
              <button ref={(element) => { itemRefs.current[0] = element; }} type="button" role="menuitem" tabIndex={-1} onKeyDown={(event) => handleItemKeyDown(event, 0)} onClick={() => run(onWorkspace)}><History size={16} /><span><strong>我的鉴伪任务</strong><small>历史记录与报告</small></span></button>
              <button ref={(element) => { itemRefs.current[1] = element; }} type="button" role="menuitem" tabIndex={-1} onKeyDown={(event) => handleItemKeyDown(event, 1)} onClick={() => run(onDeveloper)}><Code2 size={16} /><span><strong>开发者平台</strong><small>API Key 与调用用量</small></span></button>
            </div>
            <button ref={(element) => { itemRefs.current[2] = element; }} type="button" role="menuitem" tabIndex={-1} className="account-menu-logout" onKeyDown={(event) => handleItemKeyDown(event, 2)} onClick={() => run(onLogout)}><LogOut size={16} /> 退出登录</button>
          </div>
        )}
      </Presence>
    </div>
  );
}
