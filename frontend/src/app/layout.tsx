import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { cn } from "@/lib/utils";
import { Sidebar } from "@/components/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConditionalSidebar } from "@/components/conditional-sidebar";
import { UserProvider } from "@/lib/user-context";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "瓯医数链 OuMedTrust · 医疗数据要素可信流通平台",
  description: "联邦学习医疗协作 × AI数据治理 × 数据要素流通交易 —— 让医疗数据可用不可见、可控可计量",
  icons: [{ rel: "icon", url: "/logo.jpg", type: "image/jpeg" }],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={cn(geistSans.variable, geistMono.variable)}>
      <body className="antialiased">
        <UserProvider>
          <TooltipProvider>
            <ConditionalSidebar>{children}</ConditionalSidebar>
          </TooltipProvider>
        </UserProvider>
      </body>
    </html>
  );
}
