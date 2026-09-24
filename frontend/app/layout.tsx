import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"], display: "swap" });

export const metadata: Metadata = {
  title: "Asistente de seguridad",
  description: "Asistente para bloqueo de tarjetas, reportes de fraude y seguridad de tu cuenta.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Chrome Android: el teclado virtual achica el layout (y 100dvh) en vez de taparlo, así el
  // input queda siempre visible sobre el teclado.
  interactiveWidget: "resizes-content",
  viewportFit: "cover",
  themeColor: "#f8fafc",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={`${inter.variable} antialiased`}>
      <body className="font-sans">{children}</body>
    </html>
  );
}
