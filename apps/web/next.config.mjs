/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The gateway and rag_core are separate origins in dev and in production
  // (Architecture.md 10). Both are read from env so the same build runs against
  // localhost and against the deployed Mumbai box without a code change.
  env: {
    NEXT_PUBLIC_RAG_CORE_URL: process.env.NEXT_PUBLIC_RAG_CORE_URL ?? "http://127.0.0.1:8000",
    NEXT_PUBLIC_STT_GATEWAY_URL: process.env.NEXT_PUBLIC_STT_GATEWAY_URL ?? "http://127.0.0.1:8001",
  },
};
export default nextConfig;
