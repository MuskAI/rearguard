import { Component, ReactNode, useState } from "react";
import { AlertTriangle, Clipboard, RefreshCw, RotateCcw } from "lucide-react";
import HuijianBrand from "./components/HuijianBrand";

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
    <div className="startup-error-screen">
      <section className="startup-error-panel" role="alert">
        <HuijianBrand />
        <div className="startup-error-icon"><AlertTriangle size={22} /></div>
        <p className="startup-error-kicker">页面加载失败</p>
        <h1>慧鉴AI 暂时没有启动成功</h1>
        <p className="startup-error-copy">
          当前页面暂时没有启动成功。请刷新页面；如果仍然失败，换用最新版 Chrome、Edge 或 Safari 后重试。
        </p>
        <div className="startup-error-actions">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="primary-button"
          >
            <RefreshCw size={17} /> 刷新页面
          </button>
          <a className="secondary-button" href="/">
            <RotateCcw size={17} /> 返回新任务
          </a>
          <button
            type="button"
            onClick={copyDiagnostics}
            className="secondary-button"
          >
            <Clipboard size={17} /> {copied ? "诊断信息已复制" : "复制诊断信息"}
          </button>
        </div>
      </section>
    </div>
  );
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("Unified Agent frontend crashed", messageOf(error));
  }

  render() {
    if (this.state.error) return <StartupError error={this.state.error} />;
    return this.props.children;
  }
}
