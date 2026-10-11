import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the app root: a stray lockfile in a parent folder otherwise makes
  // Next.js guess the wrong workspace root.
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;
