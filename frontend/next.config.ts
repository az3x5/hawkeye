import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  experimental: {
    serverActions: {
      // The enrolment API accepts images up to 10 MiB. Allow enough room for
      // multipart fields and metadata when the home uploader forwards one.
      bodySizeLimit: "12mb",
    },
  },
  // No rewrites: a rewrite destination is baked in at build time and cannot
  // follow an environment variable set at deploy time. The browser reaches the
  // API through src/app/api/v1/[...path]/route.ts, which resolves the address
  // per request and forwards only the paths a reviewer actually needs.
};

export default config;
