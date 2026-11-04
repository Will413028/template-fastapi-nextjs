import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "template-fastapi-nextjs",
    template: "%s | template-fastapi-nextjs",
  },
  description:
    "A production-ready fullstack template with Next.js, FastAPI, and Docker Compose.",
  openGraph: {
    title: "template-fastapi-nextjs",
    description:
      "A production-ready fullstack template with Next.js, FastAPI, and Docker Compose.",
    type: "website",
    locale: "en",
  },
  twitter: {
    card: "summary",
    title: "template-fastapi-nextjs",
    description:
      "A production-ready fullstack template with Next.js, FastAPI, and Docker Compose.",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
