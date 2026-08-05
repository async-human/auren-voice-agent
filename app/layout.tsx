import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { Fraunces, Inter_Tight } from "next/font/google";
import "./globals.css";

const fraunces = Fraunces({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-serif",
  axes: ["SOFT", "WONK", "opsz"],
});

const interTight = Inter_Tight({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "Auren",
  description: "A personal voice companion that remembers with your consent",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <ClerkProvider
      signInFallbackRedirectUrl="/talk"
      signUpFallbackRedirectUrl="/talk"
      afterSignOutUrl="/"
    >
      <html lang="en" className={`${fraunces.variable} ${interTight.variable}`}>
        <body>{children}</body>
      </html>
    </ClerkProvider>
  );
}
