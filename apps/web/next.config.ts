import type { NextConfig } from "next";

/**
 * The frontend never talks to the SDN controller directly. Every request goes
 * to the FastAPI backend, which owns the lab bridge, the safety engine and the
 * audit trail. During development the backend runs on 127.0.0.1:8000 and the
 * Next dev server proxies to it, so the browser only ever needs one origin.
 *
 * Override with NEXT_PUBLIC_API_ORIGIN when the backend is not on the default
 * local address. No credentials belong in this file.
 */
const apiOrigin = (process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,

  async rewrites() {
    return [
      // Namespaced API surface: /api/runs -> <origin>/api/runs
      { source: "/api/:path*", destination: `${apiOrigin}/api/:path*` },
      // Root-level FastAPI routes: /backend/health -> <origin>/health
      { source: "/backend/:path*", destination: `${apiOrigin}/:path*` },
    ];
  },
};

export default nextConfig;
