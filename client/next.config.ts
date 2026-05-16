import path from "path";
import type { NextConfig } from "next";
import { loadEnvConfig } from "@next/env";

// Load env from repo root (shared with Python) then `client/` — latter wins on duplicate keys.
const repoRoot = path.resolve(__dirname, "..");
const clientDir = __dirname;
loadEnvConfig(repoRoot);
loadEnvConfig(clientDir);

const backendOrigin = process.env.AUTOPILOT_API_ORIGIN || "http://127.0.0.1:8090";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    root: process.cwd(),
  },
  async rewrites() {
    return [
      {
        source: "/health",
        destination: `${backendOrigin}/health`,
      },
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
      {
        source: "/demo/:path*",
        destination: `${backendOrigin}/demo/:path*`,
      },
      {
        source: "/webhooks/:path*",
        destination: `${backendOrigin}/webhooks/:path*`,
      },
      {
        source: "/oauth/:path*",
        destination: `${backendOrigin}/oauth/:path*`,
      },
    ];
  },
};

export default nextConfig;
