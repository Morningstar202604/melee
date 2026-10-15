import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: false,
  partialPrefetching: false,
  allowedDevOrigins: ['127.0.0.1'],
  // pyodide 必须保持原生 require：其内部用动态 import() 加载 WASM/标准库，
  // 被 Turbopack 打进 bundle 后这些调用会被替换成抛错桩
  // （"Cannot find module as expression is too dynamic"），沙箱整个不可用。
  serverExternalPackages: ['pyodide'],
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
