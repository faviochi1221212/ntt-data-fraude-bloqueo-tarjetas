import type { NextConfig } from "next";

// Backend FastAPI. El navegador nunca lo llama directo: pide /api/v1/* a este servidor de Next
// y el rewrite lo reenvía desde el servidor (sin CORS, y funciona desde un celular en la red local).
const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiUrl}/api/v1/:path*` }];
  },
  experimental: {
    // Un turno puede encadenar clasificación, generación, verificador y reintentos contra Groq:
    // el timeout por defecto del proxy (30 s) corta respuestas válidas.
    proxyTimeout: 120_000,
  },
  // `next dev --hostname 0.0.0.0` solo permite localhost: el celular entra por la IP privada.
  allowedDevOrigins: ["192.168.*.*", "10.*.*.*", "172.*.*.*"],
};

export default nextConfig;
