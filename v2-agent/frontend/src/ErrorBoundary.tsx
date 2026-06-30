import { Component, ReactNode, useState } from "react";

type Props = {
  children: ReactNode;
};

type State = {
  error: Error | null;
};

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "前端运行时异常";
}

function diagnosticsOf(error: unknown): string {
  if (error instanceof Error) {
    return [error.name, error.message, error.stack].filter(Boolean).join("\n");
  }
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error, null, 2);
  } catch {
    return "无法序列化诊断信息";
  }
}

export function StartupError({ error }: { error: unknown }) {
  const [copied, setCopied] = useState(false);
  const diagnostics = diagnosticsOf(error);

  async function copyDiagnostics() {
    try {
      await navigator.clipboard.writeText(diagnostics);
      setCopied(true);
    } catch {
      window.prompt("复制诊断信息", diagnostics);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-[#f2f7f4] px-4 text-[#201813]">
      <div className="w-full max-w-lg rounded-lg border border-[#c8d8d1] bg-white p-6 shadow-sm">
        <div className="text-sm font-semibold text-[#c7392f]">页面加载失败</div>
        <h1 className="mt-2 text-2xl font-semibold">鉴真 AI 鉴伪工作台未能启动</h1>
        <p className="mt-3 text-sm leading-7 text-[#5f6d66]">
          当前页面暂时没有启动成功。请刷新页面；如果仍然失败，换用最新版 Chrome、Edge 或 Safari 后重试。
        </p>
        <p className="mt-3 rounded-md bg-[#f5f8f6] p-3 text-xs leading-6 text-[#40534a]">
          如需反馈问题，可以复制诊断信息发送给维护人员。
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-lg bg-[#23648c] px-4 py-2 text-sm font-medium text-white"
          >
            刷新页面
          </button>
          <a className="rounded-lg border border-[#b8cbc2] px-4 py-2 text-sm text-[#201813]" href="/v2/?force=1">
            重新进入工作台
          </a>
          <button
            type="button"
            onClick={copyDiagnostics}
            className="rounded-lg border border-[#b8cbc2] px-4 py-2 text-sm text-[#201813]"
          >
            {copied ? "诊断信息已复制" : "复制诊断信息"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("V2 frontend crashed", messageOf(error));
  }

  render() {
    if (this.state.error) return <StartupError error={this.state.error} />;
    return this.props.children;
  }
}
