import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root to this app so Next doesn't get confused by a
  // stray lockfile elsewhere on the machine (build is always run from here).
  turbopack: { root: process.cwd() },
  // Emit a self-contained server bundle so the Docker image doesn't need to
  // ship node_modules. See dashboard/Dockerfile.
  output: "standalone",
};

export default nextConfig;
