import type { NextConfig } from "next";

const backendOrigin = process.env.AUTOPILOT_API_ORIGIN || "http://127.0.0.1:8090";

const nextConfig: NextConfig = {
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
    ];
  },
};

export default nextConfig;
