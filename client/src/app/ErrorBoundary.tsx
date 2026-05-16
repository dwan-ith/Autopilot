"use client";

import React from "react";

interface Props {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  /** Optional label to include in error logging (e.g. "Dashboard", "MissionPanel") */
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * ErrorBoundary — catches rendering errors anywhere in the tree below.
 *
 * Usage:
 *   <ErrorBoundary name="MissionPanel">
 *     <MissionPanel />
 *   </ErrorBoundary>
 */
export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    const label = this.props.name ?? "unknown";
    // Log to console so DevTools show the full stacktrace even in production mode
    console.error(`[AUTOPILOT ErrorBoundary:${label}]`, error, info.componentStack);

    // Non-blocking: report to backend trace endpoint if available
    try {
      fetch("/api/trace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: `ui.error_boundary.${label}`,
          status: "failed",
          payload: {
            error: error.message,
            stack: (error.stack ?? "").slice(0, 500),
            component: info.componentStack?.slice(0, 300),
          },
        }),
      }).catch(() => undefined); // fire-and-forget; never throws
    } catch {
      // Never let reporting break the boundary
    }
  }

  reset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      const label = this.props.name ?? "Component";
      return (
        <div
          role="alert"
          style={{
            margin: "1rem",
            padding: "1.25rem 1.5rem",
            borderRadius: "0.5rem",
            background: "rgba(239, 68, 68, 0.08)",
            border: "1px solid rgba(239, 68, 68, 0.35)",
            color: "#f87171",
            fontFamily: "monospace",
            fontSize: "0.85rem",
            lineHeight: 1.5,
          }}
        >
          <strong style={{ display: "block", marginBottom: "0.4rem", fontSize: "0.9rem" }}>
            ⚠ {label} encountered an error
          </strong>
          <span style={{ opacity: 0.75 }}>
            {this.state.error?.message ?? "Unknown error"}
          </span>
          <br />
          <button
            onClick={this.reset}
            style={{
              marginTop: "0.75rem",
              padding: "0.3rem 0.75rem",
              borderRadius: "0.35rem",
              background: "rgba(239, 68, 68, 0.18)",
              border: "1px solid rgba(239, 68, 68, 0.4)",
              color: "#fca5a5",
              cursor: "pointer",
              fontSize: "0.8rem",
            }}
          >
            Retry
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
