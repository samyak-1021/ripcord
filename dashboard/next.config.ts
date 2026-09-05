import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root to this app so Next doesn't get confused by a
  // stray lockfile elsewhere on the machine (build is always run from here).
  turbopack: { root: process.cwd() },
};

export default nextConfig;
