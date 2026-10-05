import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@statman/core", "@statman/db"],
};

export default nextConfig;
