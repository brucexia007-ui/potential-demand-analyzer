/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    // 服务器端代理使用内部服务名（Docker 网络）
    const API_BASE = process.env.API_SERVER_URL ?? "http://backend:8000";
    return [{ source: "/api/:path*", destination: `${API_BASE}/api/:path*` }];
  },
};

module.exports = nextConfig;
