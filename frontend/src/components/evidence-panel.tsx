"use client";

/**
 * 证据追溯面板（P2-4 可解释性）
 * 点击任意 AI 结论可展开"为什么这么说"的证据链。
 *
 * 用法：<EvidencePanel evidence={[{type:"policy_source",title:...}]} />
 */

import { useState, Fragment } from "react";
import { ChevronDown, FileText, Pill, Stethoscope, Activity, ShieldCheck, Database } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Evidence {
  type?: string;
  title?: string;
  source?: string;
  name?: string;
  disease?: string;
  amount?: number;
  count?: number;
  [key: string]: unknown;
}

const typeConfig: Record<string, { label: string; icon: typeof FileText; color: string }> = {
  policy_source: { label: "政策依据", icon: FileText, color: "text-blue-600 bg-blue-50" },
  medication: { label: "用药记录", icon: Pill, color: "text-purple-600 bg-purple-50" },
  diagnosis: { label: "诊断记录", icon: Stethoscope, color: "text-red-600 bg-red-50" },
  drug_disease: { label: "慢病诊断", icon: Activity, color: "text-orange-600 bg-orange-50" },
  chronic_disease: { label: "慢病诊断", icon: Activity, color: "text-orange-600 bg-orange-50" },
  drug_cost: { label: "费用数据", icon: Database, color: "text-green-600 bg-green-50" },
  annual_cost: { label: "年度费用", icon: Database, color: "text-green-600 bg-green-50" },
  age: { label: "年龄因素", icon: Activity, color: "text-indigo-600 bg-indigo-50" },
  agent_source: { label: "智能体来源", icon: ShieldCheck, color: "text-cyan-600 bg-cyan-50" },
  visit_count: { label: "就诊统计", icon: Stethoscope, color: "text-rose-600 bg-rose-50" },
  policy_keyword_match: { label: "关键词匹配", icon: FileText, color: "text-blue-600 bg-blue-50" },
};

export function EvidencePanel({
  evidence,
  label = "为什么这么说？",
}: {
  evidence?: Evidence[] | null;
  label?: string;
}) {
  const [open, setOpen] = useState(false);

  if (!evidence || evidence.length === 0) return null;

  return (
    <div className="mt-2 border-t border-slate-100 pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-medium text-slate-500 transition hover:text-cyan-600"
      >
        <ChevronDown
          className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")}
        />
        {label}
        <span className="rounded-full bg-slate-100 px-1.5 text-[10px] text-slate-600">
          {evidence.length} 项证据
        </span>
      </button>

      {open && (
        <div className="mt-2 space-y-1.5">
          {evidence.map((e, i) => {
            const cfg = typeConfig[e.type || ""] || {
              label: "数据依据",
              icon: Database,
              color: "text-slate-600 bg-slate-50",
            };
            const Icon = cfg.icon;
            const desc = _describe(e);
            return (
              <div
                key={i}
                className="flex items-start gap-2 rounded-md bg-slate-50 px-2.5 py-1.5 text-xs"
              >
                <span className={cn("flex h-5 w-5 shrink-0 items-center justify-center rounded", cfg.color)}>
                  <Icon className="h-3 w-3" />
                </span>
                <div className="flex-1">
                  <span className="font-medium text-slate-700">{cfg.label}：</span>
                  <span className="text-slate-600">{desc}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function _describe(e: Evidence): string {
  if (e.title && e.source) return `${e.title}（来源：${e.source}）`;
  if (e.title) return e.title;
  if (e.name && e.disease) return `${e.name}（${e.disease}）`;
  if (e.name) return e.name;
  if (e.disease) return `诊断：${e.disease}`;
  if (typeof e.amount === "number") return `金额 ${e.amount} 元`;
  if (typeof e.count === "number") return `${e.count} 次/项`;
  // 兜底：展示所有非 type 字段
  const parts = Object.entries(e)
    .filter(([k]) => k !== "type")
    .map(([k, v]) => `${k}=${String(v)}`);
  return parts.join("，") || "数据依据";
}
