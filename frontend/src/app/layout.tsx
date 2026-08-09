import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Rewind",
  description: "Longitudinal learning archive",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <nav className="flex gap-4 border-b border-black/10 px-6 py-3 text-sm dark:border-white/15">
          <Link href="/" className="font-medium">
            Rewind
          </Link>
          <Link href="/upload" className="opacity-70 hover:opacity-100">
            Upload
          </Link>
        </nav>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
