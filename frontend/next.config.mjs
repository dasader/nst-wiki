/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const api = process.env.API_URL || "http://api:8000";
    return ["/api/v1/query", "/api/v1/wiki", "/api/v1/wiki/:path*", "/api/v1/data/:path*"]
      .map((source) => ({ source, destination: `${api}${source}` }));
  },
};
export default nextConfig;
