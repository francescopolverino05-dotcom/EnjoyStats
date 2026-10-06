import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "STAT MAN",
  description: "Auto-tag football matches and export Once Sport Analyser XMLs",
};

const links = [
  { href: "/", label: "Home" },
  { href: "/setup", label: "Match setup" },
  { href: "/tagging", label: "Tagging" },
  { href: "/review", label: "Review" },
  { href: "/stats", label: "Stats" },
  { href: "/export", label: "Export" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <main>
          <nav className="nav">
            <strong>STAT MAN</strong>
            {links.map((link) => (
              <Link key={link.href} href={link.href}>
                {link.label}
              </Link>
            ))}
          </nav>
          {children}
        </main>
      </body>
    </html>
  );
}
