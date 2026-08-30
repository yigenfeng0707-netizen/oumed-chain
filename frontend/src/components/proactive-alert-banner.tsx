"use client";

/**
 * 主动健康预警横幅（P2-3 范式创新）
 * 用户进入首页时主动调用 proactive-alerts，弹出预警卡片。
 * 体现“瓯医数链主动关心你”——不是被动问答。
 */

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Bell, X, AlertTriangle, ChevronRight, Brain } from "lucide-react";
import { useUser } from "@/lib/user-context";
import { getProactiveAlerts, type ProactiveAlert } from "@/lib/api";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";

export function ProactiveAlertBanner() {
  const { userId, currentUser } = useUser();
  const router = useRouter();
  const [alerts, setAlerts] = useState<ProactiveAlert[]>([]);
  const [dismissed, setDismissed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDismissed(false);
    setExpanded(false);
    getProactiveAlerts(userId).then((data) => {
      if (!cancelled) setAlerts(data);
    });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  // 切换用户后重置 dismissed
  useEffect(() => {
    setDismissed(false);
  }, [currentUser.id]);

  if (dismissed || alerts.length === 0) return null;

  const highCount = alerts.filter((a) => a.level === "high").length;
  const hasHigh = highCount > 0;
  const eegAlerts = alerts.filter((a) => (a as ProactiveAlert & { source?: string }).source === "eeg");
  const hasEeg = eegAlerts.length > 0;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: -20, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -20 }}
        transition={{ type: "spring", stiffness: 300, damping: 25 }}
        className={cn(
          "mx-auto mb-4 w-full max-w-3xl overflow-hidden rounded-xl border shadow-lg",
          hasHigh
            ? "border-red-200 bg-gradient-to-r from-red-50 to-orange-50"
            : "border-amber-200 bg-gradient-to-r from-amber-50 to-yellow-50"
        )}
      >
        {/* 头部 */}
        <div className="flex items-center gap-3 px-4 py-3">
          <div
            className={cn(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
              hasHigh ? "bg-red-100" : "bg-amber-100"
            )}
          >
            {hasHigh ? (
              <AlertTriangle className="h-5 w-5 text-red-600" />
            ) : (
              <Bell className="h-5 w-5 text-amber-600" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">
                瓯医数链主动提醒
              </span>
              {hasHigh && (
                <span className="rounded-full bg-red-600 px-1.5 py-0.5 text-[10px] font-bold text-white">
                  {highCount} 项紧急
                </span>
              )}
            </div>
            <p className="text-xs text-slate-600">
              为 <span className="font-medium">{currentUser.name}</span> 检测到{" "}
              {alerts.length} 项需要关注的健康预警
              {hasEeg && <span className="text-fuchsia-600">（含 {eegAlerts.length} 项脑电预警）</span>}
            </p>
          </div>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="rounded-lg bg-white/70 px-2.5 py-1 text-xs font-medium text-slate-700 transition hover:bg-white"
          >
            {expanded ? "收起" : "查看"}
          </button>
          <button
            onClick={() => setDismissed(true)}
            className="text-slate-400 transition hover:text-slate-600"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 展开的预警列表 */}
        <AnimatePresence>
          {expanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="border-t border-slate-200/60"
            >
              <div className="max-h-64 overflow-y-auto px-4 py-2">
                {alerts.map((a, i) => {
                  const isEeg = (a as ProactiveAlert & { source?: string }).source === "eeg";
                  return (
                  <div
                    key={i}
                    className="flex items-start gap-2.5 border-b border-slate-100 py-2 last:border-0"
                  >
                    <span className="text-base leading-none">{a.icon}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold text-slate-800">
                          {a.title}
                        </span>
                        {isEeg && (
                          <span className="inline-flex items-center gap-0.5 rounded-full bg-fuchsia-100 px-1.5 py-0.5 text-[10px] font-medium text-fuchsia-700">
                            <Brain className="h-2.5 w-2.5" />
                            脑电
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-slate-600 line-clamp-2">
                        {a.description || a.desc}
                      </div>
                      {(a.suggestion || a.action) && (
                        <div className="mt-0.5 text-[11px] text-cyan-600">
                          💡 {a.suggestion || a.action}
                        </div>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>
              <div className="flex w-full">
                <button
                  onClick={() => router.push("/health")}
                  className="flex flex-1 items-center justify-center gap-1 bg-white/50 py-2 text-xs font-medium text-cyan-600 transition hover:bg-white"
                >
                  健康画像
                  <ChevronRight className="h-3 w-3" />
                </button>
                {hasEeg && (
                  <button
                    onClick={() => router.push("/eeg")}
                    className="flex flex-1 items-center justify-center gap-1 border-l border-slate-200/60 bg-white/50 py-2 text-xs font-medium text-fuchsia-600 transition hover:bg-white"
                  >
                    <Brain className="h-3 w-3" />
                    脑电健康
                    <ChevronRight className="h-3 w-3" />
                  </button>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </AnimatePresence>
  );
}
