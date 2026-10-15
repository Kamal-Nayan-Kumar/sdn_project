/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  async rewrites() {
    const apiOrigin = (process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

    return [
      // Ops routes such as /health live at the root, not under /v1
      { source: "/backend/health", destination: `${apiOrigin}/health` },
      // All other backend routes go through the /v1 prefix (lab routes are under /v1)
      { source: "/backend/:path*", destination: `${apiOrigin}/v1/:path*` },
      // Lab API bridge: /api/lab/* -> <origin>/v1/* (so /api/lab/snapshot -> /v1/snapshot)
      { source: "/api/lab/:path*", destination: `${apiOrigin}/v1/:path*` },
      // Agent runs API: /api/runs -> <origin>/api/runs
      { source: "/api/runs", destination: `${apiOrigin}/api/runs` },
      { source: "/api/runs/:path*", destination: `${apiOrigin}/api/runs/:path*` },
    ];
  },
};

module.exports = nextConfig;
