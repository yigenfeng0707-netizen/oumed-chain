"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { DidaYiLogo } from "@/components/didayi-logo";
import {
  Home,
  Shield,
  Heart,
  FileText,
  BookOpen,
  Lock,
  Network,
  Brain,
  ScanLine,
  Accessibility,
  LayoutDashboard,
  Share2,
  FileSearch,
  ShoppingBag,
} from "lucide-react";

const navItems = [
  { href: "/", label: "首页", icon: Home },
  { href: "/federation", label: "联邦协作网络", icon: Share2 },
  { href: "/governance", label: "AI病历治理", icon: FileSearch },
  { href: "/marketplace", label: "数据要素市场", icon: ShoppingBag },
  { href: "/coverage", label: "权益全景", icon: Shield },
  { href: "/health", label: "健康画像", icon: Heart },
  { href: "/body-archive", label: "数字人体档案", icon: Accessibility },
  { href: "/eeg", label: "脑电健康", icon: Brain },
  { href: "/imaging", label: "影像标注", icon: ScanLine },
  { href: "/claims", label: "报销预审", icon: FileText },
  { href: "/policy", label: "政策匹配", icon: BookOpen },
  { href: "/security", label: "数据授权", icon: Lock },
  { href: "/security/data-space", label: "可信数据空间", icon: Network },
  { href: "/admin", label: "管理后台", icon: LayoutDashboard },
];

/** 导航主体：桌面侧栏与移动端抽屉共用。onNavigate 用于抽屉内点击后关闭。 */
export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[84px] items-center border-b border-sky-100 px-5">
        <DidaYiLogo />
      </div>
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 py-4">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={cn(
                "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all",
                isActive
                  ? "bg-gradient-to-r from-cyan-400 to-sky-500 text-white shadow-lg shadow-cyan-500/20"
                  : "text-slate-600 hover:bg-white/80 hover:text-cyan-700"
              )}
            >
              <item.icon className={cn("h-5 w-5", isActive && "text-white")} />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-sky-100 bg-white/45 p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-100 text-cyan-600">
            <Heart className="h-4 w-4" />
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-medium text-slate-700">
              瓯医数链助手
            </span>
            <span className="text-xs leading-relaxed text-slate-400">
              关键医疗信号识别 × 患者信息连接
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 桌面端（lg 及以上）固定侧栏；小屏由 ConditionalSidebar 渲染抽屉。 */
export function Sidebar() {
  return (
    <aside className="fixed left-0 top-0 z-40 hidden h-screen w-64 border-r border-sky-100 bg-[linear-gradient(180deg,#f8fdff_0%,#eef9ff_58%,#e8f6fd_100%)] shadow-[12px_0_32px_rgba(49,142,184,.10)] lg:block">
      <SidebarNav />
    </aside>
  );
}
