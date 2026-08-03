"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error("[ErrorBoundary]", error);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex min-h-screen items-center justify-center">
          <div className="max-w-md rounded-lg border border-neutral-950/10 bg-white/90 p-8 text-center shadow-[var(--shadow-panel)]">
            <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-lg border border-red-200 bg-red-50 text-xs font-semibold text-red-700">
              ERR
            </div>
            <h2 className="mb-2 text-lg font-semibold text-neutral-950">
              页面渲染出错
            </h2>
            <p className="mb-6 text-sm text-neutral-600">
              {this.state.error?.message || "发生了未知错误"}
            </p>
            <button
              onClick={() => {
                this.setState({ hasError: false, error: null });
                window.location.reload();
              }}
              className="rounded-full border border-neutral-950 bg-neutral-950 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-neutral-800"
            >
              重新加载
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
