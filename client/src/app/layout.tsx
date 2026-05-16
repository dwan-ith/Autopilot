import type { Metadata } from "next";
import "./globals.css";
import { ErrorBoundary } from "./ErrorBoundary";

export const metadata: Metadata = {
  title: "AUTOPILOT | Autonomous Operator Runtime",
  description: "Advanced autonomous operator runtime for connected systems.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <ErrorBoundary name="RootLayout">
          {children}
        </ErrorBoundary>
      </body>
    </html>
  );
}
