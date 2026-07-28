import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Pixii Intelligence",
  description: "Content generation, improvement and analysis for Pixii.",
};

const NAV = [
  { href: "/", label: "Status" },
  { href: "/posts", label: "Corpus" },
  { href: "/templates", label: "Templates" },
  { href: "/studio", label: "Studio" },
  { href: "/scoreboard", label: "Scoreboard" },
];

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
        <nav className="border-b border-black/10 dark:border-white/15">
          <div className="mx-auto flex max-w-5xl gap-5 px-6 py-3 text-sm">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="opacity-70 hover:opacity-100">
                {item.label}
              </Link>
            ))}
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
