'use client';

import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
    children?: ReactNode;
    fallback?: ReactNode;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false,
        error: null,
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('Uncaught error:', error, errorInfo);
    }

    public render() {
        if (this.state.hasError) {
            if (this.props.fallback) {
                return this.props.fallback;
            }
            return (
                <div className="flex h-screen w-full flex-col items-center justify-center bg-black/90 p-8 text-neutral-200">
                    <div className="flex max-w-lg flex-col gap-4 rounded-xl border border-red-500/20 bg-red-950/20 p-6 backdrop-blur-sm">
                        <h2 className="text-xl font-medium text-red-400">Connection Error</h2>
                        <p className="text-sm text-neutral-400">
                            The dashboard lost connection to the Autopilot runtime backend or encountered a fatal UI crash.
                        </p>
                        <div className="mt-2 text-xs font-mono text-neutral-500 opacity-80 break-all">
                            {this.state.error?.message}
                        </div>
                        <button
                            onClick={() => {
                                this.setState({ hasError: false, error: null });
                                window.location.reload();
                            }}
                            className="mt-4 w-fit rounded-lg bg-orange-600/20 px-4 py-2 text-sm font-medium text-orange-400 transition-colors hover:bg-orange-600/30"
                        >
                            Reload Dashboard
                        </button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}
