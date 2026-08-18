import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // No rewrites: a rewrite destination is baked in at build time and cannot
  // follow an environment variable set at deploy time. The browser reaches the
  // API through src/app/api/v1/[...path]/route.ts, which resolves the address
  // per request and forwards only the paths a reviewer actually needs.
};

export default config;
