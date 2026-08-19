import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Shruti — voice RAG, measured",
  description:
    "Ask in Hindi or English. Grounded, cited answers from MSMARCO-XI with a live per-stage latency breakdown, and a refusal when the corpus cannot answer.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* Design.md 3: Geist, with the documented fallback chain. Explicitly NOT
          Inter - the brief names it as the stale default to avoid. Loaded from
          the package's CDN build rather than next/font so the whole stack stays
          buildable offline if the network is unavailable at build time. */}
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
