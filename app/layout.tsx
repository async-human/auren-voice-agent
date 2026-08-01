import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Auren Local",
  description: "Local interface for the Auren open-source voice agent",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
