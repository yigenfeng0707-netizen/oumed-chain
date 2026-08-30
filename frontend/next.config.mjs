/** @type {import('next').NextConfig} */
const nextConfig = {
  // 本地生产验证使用独立目录，避免覆盖正在运行的开发预览样式。
  distDir: process.env.NEXT_DISABLE_STANDALONE === "1" ? ".next-build" : ".next",
  // Docker 使用 standalone；Windows 无符号链接权限时可禁用，仅用于本地构建验证。
  output: process.env.NEXT_DISABLE_STANDALONE === "1" ? undefined : "standalone",
  // 魔搭创空间同域部署：ENABLE_API_PROXY=1 时把 /api/* 代理到容器内 FastAPI(8000)
  // 其他部署（Render/Vercel）使用 NEXT_PUBLIC_API_URL 绝对地址，不走此代理
  async rewrites() {
    if (process.env.ENABLE_API_PROXY === "1") {
      return [
        {
          source: "/api/:path*",
          destination: `${process.env.BACKEND_PROXY_TARGET || "http://127.0.0.1:8000"}/api/:path*`,
        },
        {
          source: "/digital-body/:path*",
          destination: "http://127.0.0.1:8000/digital-body/:path*",
        },
      ];
    }
    return [];
  },
};

export default nextConfig;
