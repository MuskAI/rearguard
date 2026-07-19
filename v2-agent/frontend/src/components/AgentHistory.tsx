import {
  ChevronRight,
  FileText,
  History,
  Image as ImageIcon,
  LogIn,
  LogOut,
  Menu,
  Plus,
  Search,
  Trash2,
  UserRound,
  Video,
  X,
} from "lucide-react";
import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import type { AccountUser } from "../api";
import type { AgentHistoryEntry } from "../agentTypes";
import HuijianBrand from "./HuijianBrand";

interface Props {
  entries: AgentHistoryEntry[];
  activeKey?: string;
  query: string;
  loading: boolean;
  message: string;
  user: AccountUser | null;
  mobileOpen: boolean;
  onQueryChange: (value: string) => void;
  onSelect: (entry: AgentHistoryEntry) => void;
  onDelete: (entry: AgentHistoryEntry) => void;
  deletingKey?: string;
  onNew: () => void;
  onLogin: () => void;
  onLogout: () => void;
  onCloseMobile: () => void;
}

function entryIcon(entry: AgentHistoryEntry) {
  if (entry.origin === "image") return <ImageIcon size={16} />;
  if (entry.origin === "video") return <Video size={16} />;
  return <FileText size={16} />;
}

function maskPhone(phone: string) {
  return phone.replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2");
}

function HistoryContent(props: Props & { closeButtonRef?: RefObject<HTMLButtonElement> }) {
  const filtered = props.entries.filter((entry) => {
    const query = props.query.trim().toLowerCase();
    if (!query) return true;
    return [entry.title, entry.typeLabel, entry.verdictLabel, entry.createdAt].some((value) => value.toLowerCase().includes(query));
  });

  return (
    <aside className="agent-sidebar" aria-label="任务历史">
      <div className="sidebar-brand-row">
        <HuijianBrand />
        {props.mobileOpen && (
          <button ref={props.closeButtonRef} type="button" className="icon-button sidebar-mobile-close" onClick={props.onCloseMobile} aria-label="关闭历史记录" title="关闭">
            <X size={18} />
          </button>
        )}
      </div>
      <button type="button" className="new-task-button" onClick={props.onNew}>
        <Plus size={17} />
        新建鉴伪
      </button>

      <div className="sidebar-section-heading">
        <span><History size={15} /> 最近任务</span>
        {props.user && <b>{props.entries.length}</b>}
      </div>

      {props.user ? (
        <>
          <label className="history-search">
            <Search size={15} />
            <input value={props.query} onChange={(event) => props.onQueryChange(event.target.value)} placeholder="搜索文件或结论" aria-label="搜索历史任务" />
          </label>
          <div className="history-list" aria-live="polite">
            {props.loading && props.entries.length === 0 && (
              <div className="history-empty"><span className="mini-loader" />正在读取你的任务</div>
            )}
            {!props.loading && !props.message && filtered.length === 0 && (
              <div className="history-empty">{props.query ? "没有匹配的任务" : "完成鉴伪后，任务会出现在这里"}</div>
            )}
            {props.message && <div className="history-empty history-error">{props.message}</div>}
            {filtered.map((entry) => (
              <div className="history-entry-row" key={entry.key}>
                <button
                  type="button"
                  className={`history-entry ${props.activeKey === entry.key ? "active" : ""}`}
                  onClick={() => props.onSelect(entry)}
                >
                  <span className="history-thumb">
                    {entry.thumbnail ? <img src={entry.thumbnail} alt="" loading="lazy" decoding="async" fetchPriority="low" /> : entryIcon(entry)}
                  </span>
                  <span className="history-entry-copy">
                    <strong>{entry.title}</strong>
                    <span>{entry.typeLabel} · {entry.verdictLabel}</span>
                    <small>{entry.createdAt || "时间未知"}</small>
                  </span>
                  <ChevronRight size={15} className="history-chevron" />
                </button>
                <button
                  type="button"
                  className="history-delete-button"
                  onClick={() => props.onDelete(entry)}
                  disabled={props.deletingKey === entry.key}
                  aria-label={`删除历史记录：${entry.title}`}
                  title="删除记录"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="history-guest">
          <div className="history-guest-icon"><History size={20} /></div>
          <strong>登录后查看个人历史</strong>
          <p>任务按账号隔离保存，不会与其他用户混在一起。</p>
        </div>
      )}

      <div className="sidebar-account">
        {props.user ? (
          <>
            <span className="account-avatar"><UserRound size={17} /></span>
            <span className="account-copy">
              <strong>{props.user.username || "慧鉴用户"}</strong>
              <small>{maskPhone(props.user.phone || "")}</small>
            </span>
            <button type="button" className="icon-button" onClick={props.onLogout} aria-label="退出登录" title="退出登录"><LogOut size={17} /></button>
          </>
        ) : (
          <button type="button" className="sidebar-login" onClick={props.onLogin}><LogIn size={16} /> 登录或注册</button>
        )}
      </div>
      <a className="icp-link" href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">浙ICP备2026051442号</a>
    </aside>
  );
}

export default function AgentHistory(props: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(props.onCloseMobile);
  onCloseRef.current = props.onCloseMobile;

  useEffect(() => {
    if (!props.mobileOpen) return;
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.requestAnimationFrame(() => closeButtonRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => !element.hasAttribute("hidden"));
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
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
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusTimer);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      openerRef.current?.focus();
    };
  }, [props.mobileOpen]);

  return (
    <>
      <div className="sidebar-desktop"><HistoryContent {...props} /></div>
      {props.mobileOpen && (
        <div className="sidebar-mobile-layer">
          <button className="sidebar-backdrop" type="button" aria-label="关闭历史记录" onClick={props.onCloseMobile} />
          <div ref={dialogRef} className="sidebar-mobile" role="dialog" aria-modal="true" aria-label="个人任务历史" tabIndex={-1}><HistoryContent {...props} closeButtonRef={closeButtonRef} /></div>
        </div>
      )}
    </>
  );
}

export function MobileHistoryButton({ onClick }: { onClick: () => void }) {
  return <button type="button" className="icon-button mobile-history-button" onClick={onClick} aria-label="打开历史记录" title="历史记录"><Menu size={20} /></button>;
}
