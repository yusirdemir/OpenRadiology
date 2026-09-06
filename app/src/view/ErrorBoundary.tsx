import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, Home, RefreshCw } from "./icons";

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
  fallback?: ReactNode;
  onReset?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public override state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
    this.props.onReset?.();
  };

  public override render() {
    if (this.state.hasError) {
      if (this.props.fallback !== undefined) {
        return this.props.fallback;
      }
      return (
        <div className="flex h-full w-full flex-col items-center justify-center bg-ink-950 p-6 text-center text-chalk-100 select-none">
          <div className="max-w-md rounded-[10px] border border-alert-400/25 bg-ink-900/90 p-6 shadow-2xl backdrop-blur-sm">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full border border-alert-400/35 bg-alert-900">
              <AlertTriangle size={22} className="text-alert-400" aria-hidden />
            </div>
            <h2 className="text-base font-semibold text-chalk-100">
              {this.props.fallbackTitle ?? "Görsel Yüklenirken Bir Sorun Oluştu"}
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-chalk-400">
              Oturum verileriniz güvendedir. Sayfayı yenileyebilir veya çalışma alanına geri dönebilirsiniz.
            </p>
            {this.state.error && (
              <p className="mt-3 max-h-24 overflow-y-auto rounded bg-ink-950 p-2 font-mono text-[10px] text-alert-400 text-left border border-[var(--hairline)]">
                {this.state.error.message}
              </p>
            )}
            <div className="mt-5 flex items-center justify-center gap-2">
              <button
                type="button"
                className="btn btn-primary"
                onClick={this.handleReset}
              >
                <RefreshCw size={14} aria-hidden />
                Yeniden dene
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  window.location.href = window.location.pathname;
                }}
              >
                <Home size={14} aria-hidden />
                Ana sayfaya dön
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
