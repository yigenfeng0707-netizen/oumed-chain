"use client";

import { useState } from "react";
import { Menu } from "lucide-react";
import { Sidebar, SidebarNav } from "@/components/sidebar";
import { UserSwitcher } from "@/components/user-switcher";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Activity, Bell } from "lucide-react";

export function ConditionalSidebar({ children }: { children: React.ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 bg-sky-50/70 lg:ml-64">
        {/* 顶部固定栏：小屏汉堡导航 + 品牌标识 + 用户切换器 */}
        <div className="sticky top-0 z-30 flex h-14 items-center justify-between gap-2 border-b border-sky-100/90 bg-white/80 px-3 backdrop-blur-xl sm:px-6 lg:h-[72px] lg:px-7">
          <div className="flex min-w-0 items-center gap-2">
            {/* 小屏（<lg）：汉堡按钮唤起抽屉导航 */}
            <Sheet open={navOpen} onOpenChange={setNavOpen}>
              <SheetTrigger asChild>
                <button
                  aria-label="打开导航菜单"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 text-slate-600 transition-colors hover:bg-slate-100 lg:hidden"
                >
                  <Menu className="h-5 w-5" />
                </button>
              </SheetTrigger>
              <SheetContent side="left" className="w-72 bg-sidebar p-0">
                <SheetTitle className="sr-only">OuMedTrust 导航菜单</SheetTitle>
                <SidebarNav onNavigate={() => setNavOpen(false)} />
              </SheetContent>
            </Sheet>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-cyan-50 text-cyan-600">
              <Activity className="h-4 w-4" />
            </span>
            <span className="truncate text-sm font-medium text-slate-500">
              瓯医数链 · 医疗数据要素协作平台
            </span>
          </div>
          <div className="flex items-center gap-3">
            <button
              aria-label="消息提醒"
              className="relative hidden h-10 w-10 items-center justify-center rounded-xl border border-sky-100 bg-white text-slate-500 shadow-sm transition hover:bg-sky-50 hover:text-cyan-600 sm:flex"
            >
              <Bell className="h-4 w-4" />
              <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-[#FF7A59]" />
            </button>
            <UserSwitcher />
          </div>
        </div>
        {children}
      </main>
    </div>
  );
}
