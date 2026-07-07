/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const api = process.env.API_URL || "http://api:8000";
    const apiPaths = [
      "/api/v1/query", "/api/v1/wiki", "/api/v1/wiki/:path*", "/api/v1/data/:path*",
      "/api/v1/ingest", "/api/v1/ingest/:path*", "/api/v1/admin/:path*",
    ].map((source) => ({ source, destination: `${api}${source}` }));
    return apiPaths;
  },
};
export default nextConfig;
