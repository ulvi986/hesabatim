import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // /api is proxied to FastAPI by src/proxy.ts, which also guards the admin panel.
  poweredByHeader: false,
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
