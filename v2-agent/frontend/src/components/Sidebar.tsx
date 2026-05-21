import { HistoryItem, VERDICT_META, TYPE_LABEL } from "../api";
import Logo from "./Logo";

interface Props {
  history: HistoryItem[];
  activeId?: string;
  onSelect: (item: HistoryItem) => void;
  onNew: () => void;
  onDelete: (taskId: string) => void;
}

const TYPE_ICON: Record<string, string> = {
  image: "🖼", video: "🎬", audio: "🎵", document: "📄",
};

export default function Sidebar({ history, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <aside className="w-64 shrink-0 bg-ink-900 border-r border-ink-700 flex flex-col shadow-sm">
      <div className="p-4 flex items-center gap-2.5">
        <Logo size={36} idSuffix="side" />
        <div>
          <div className="font-serif text-xl font-semibold text-rice leading-tight tracking-[0.15em]">鉴真</div>
          <div className="text-[10px] text-cinnabar-light tracking-[0.2em]">AI 鉴伪智能体</div>
        </div>
      </div>

      <button
        onClick={onNew}
        className="mx-4 mb-3 py-2 rounded-lg bg-cinnabar text-white text-sm hover:bg-cinnabar-dark transition shadow-sm"
      >
        + 新建检测
      </button>

      <div className="px-4 pb-1 text-[11px] text-ink-500 uppercase tracking-wider">历史记录</div>
      <div className="flex-1 overflow-y-auto px-2 space-y-1">
        {history.length === 0 && (
          <div className="px-2 py-4 text-xs text-ink-500">暂无记录</div>
        )}
        {history.map((h) => {
          const meta = VERDICT_META[h.verdict];
          return (
            <div
              key={h.taskId}
              onClick={() => onSelect(h)}
              className={`group px-2.5 py-2 rounded-lg cursor-pointer flex items-center gap-2 ${
                activeId === h.taskId ? "bg-ink-700" : "hover:bg-ink-800"
              }`}
            >
              <span className="text-base">{TYPE_ICON[h.type] ?? "📎"}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-ink-950 truncate">{h.name}</div>
                <div className="text-[10px] flex items-center gap-1.5">
                  <span style={{ color: meta.color }}>{meta.label}</span>
                  <span className="text-ink-500">· {TYPE_LABEL[h.type]}</span>
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(h.taskId);
                }}
                className="opacity-0 group-hover:opacity-100 text-ink-500 hover:text-verdict-fake text-xs px-1"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>

      <div className="p-3 text-[10px] text-ink-500 border-t border-ink-700">
        鉴真伪 · 明真相
      </div>
    </aside>
  );
}
