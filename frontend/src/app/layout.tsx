import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppHeader } from "@/components/app-header";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
});

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  axes: ["SOFT", "WONK", "opsz"],
});

export const metadata: Metadata = {
  title: "OmicsBase",
  description:
    "AI-powered microbiome analysis with transparent, reproducible Quarto reports.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} ${fraunces.variable} antialiased`}>
        <Providers>
          <TooltipProvider>
            <div className="min-h-screen bg-background text-foreground">
              <AppHeader />
              <main>{children}</main>
            </div>
          </TooltipProvider>
        </Providers>
      </body>
    </html>
  );
}
