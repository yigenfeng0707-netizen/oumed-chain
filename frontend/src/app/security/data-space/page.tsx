"use client";

/**
 * 可信数据空间可视化页（P2-2 核心创新点）
 *
 * 把"可信数据空间"从 PPT 概念变成可交互演示：
 * - 数据流转可视化（用户 → 授权 → Agent → 数据源 → 隐私计算 → 存证）
 * - "数据可用不可见"演示（原始数据 vs 脱敏数据对比）
 * - 区块链存证模拟（访问日志哈希串联成链）
 */

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  ShieldCheck,
  Lock,
  Database,
  Eye,
  EyeOff,
  Fingerprint,
  Boxes,
  CheckCircle2,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { useUser } from "@/lib/user-context";
import { getDataFlow, listFederationJobs, listDataTransactions } from "@/lib/api";
import { cn } from "@/lib/utils";
import { BrandedPageHeader } from "@/components/branded-page-header";

interface FlowStep {
  step: string;
  actor: string;
  status: string;
  detail: string;
  ts: string;
}
interface Flow {
  id: string;
  data_type: string;
  agent: string;
  steps: FlowStep[];
}

const agentLabel: Record<string, string> = {
  coverage_agent: "权益管家",
  health_agent: "健康卫士",
  claims_agent: "报销助手",
  policy_agent: "政策参谋",
};

// 原始数据 vs 脱敏数据对比演示
const privacyDemo = [
  {
    field: "姓名",
    raw: "张丽华",
    masked: "张*华",
  },
  {
    field: "身份证号",
    raw: "320106196803150028",
    masked: "320106********0028",
  },
  {
    field: "诊断记录",
    raw: "2型糖尿病（E11.9），空腹血糖 9.2mmol/L",
    masked: "内分泌代谢异常（代码化），指标偏离正常范围",
  },
  {
    field: "联系电话",
    raw: "13856237891",
    masked: "138****7891",
  },
  {
    field: "家庭住址",
    raw: "某市某区某街道88号3栋502室",
    masked: "某市某区（区域级）",
  },
];

export default function DataSpacePage() {
  const { userId, currentUser } = useUser();
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loading, setLoading] = useState(true);
  const [showRaw, setShowRaw] = useState(false);
  const [expandedFlow, setExpandedFlow] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getDataFlow(userId)
      .then((data) => {
        setFlows(data?.flows || []);
      })
      .finally(() => setLoading(false));
  }, [userId]);

  return (
    <div className="didayi-page">
      <div className="mx-auto max-w-6xl space-y-6">
        {/* 页头 */}
        <BrandedPageHeader
          title="可信数据空间"
          description={<>数据可用不可见 · 原始数据不出域 · 全链路可追溯（为 <span className="font-semibold text-slate-700">{currentUser.name}</span> 提供）</>}
          badge="可信流转"
        />

        {/* 核心理念 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[
            { icon: EyeOff, title: "数据可用不可见", desc: "智能体获取计算结果，无法看到原始明细", color: "from-cyan-400 to-cyan-600" },
            { icon: Lock, title: "原始数据不出域", desc: "数据在沙箱内计算，不离开可信数据空间", color: "from-sky-500 to-[#0876a8]" },
            { icon: Fingerprint, title: "全链路可追溯", desc: "每次访问区块链存证，哈希不可篡改", color: "from-[#ff9b7f] to-[#ff7051]" },
          ].map((item, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.1 }}
              className="didayi-card p-5"
            >
              <div className={cn("mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br text-white", item.color)}>
                <item.icon className="h-5 w-5" />
              </div>
              <h3 className="font-semibold text-slate-900">{item.title}</h3>
              <p className="mt-1 text-xs text-slate-600">{item.desc}</p>
            </motion.div>
          ))}
        </div>

        {/* 隐私计算演示：原始数据 vs 脱敏数据 */}
        <div className="didayi-card p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-cyan-600" />
              <h2 className="text-lg font-bold text-slate-900">隐私计算：可用不可见</h2>
            </div>
            <button
              onClick={() => setShowRaw((v) => !v)}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition",
                showRaw
                  ? "bg-red-50 text-red-600 hover:bg-red-100"
                  : "bg-cyan-50 text-cyan-600 hover:bg-cyan-100"
              )}
            >
              {showRaw ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
              {showRaw ? "显示原始数据（危险）" : "仅显示脱敏数据（安全）"}
            </button>
          </div>
          <p className="mb-3 text-xs text-slate-500">
            智能体在可信沙箱内只能看到脱敏后的数据，无法接触原始个人信息。
            切换开关对比两种数据呈现。
          </p>
          <div className="overflow-x-auto rounded-xl border border-slate-200">
            <table className="w-full min-w-[420px] text-sm">
              <thead className="bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="px-4 py-2.5 text-left font-medium">字段</th>
                  <th className="px-4 py-2.5 text-left font-medium">
                    {showRaw ? "原始数据（不应被智能体看到）" : "智能体可见（脱敏后）"}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {privacyDemo.map((row) => (
                  <tr key={row.field} className={showRaw ? "bg-red-50/40" : ""}>
                    <td className="px-4 py-2.5 font-medium text-slate-700">{row.field}</td>
                    <td className={cn(
                      "px-4 py-2.5 font-mono text-xs",
                      showRaw ? "text-red-600" : "text-slate-600"
                    )}>
                      {showRaw ? row.raw : row.masked}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={cn(
            "mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs",
            showRaw ? "bg-red-50 text-red-700" : "bg-cyan-50 text-cyan-700"
          )}>
            {showRaw ? "⚠️ 智能体若能看到原始数据，存在隐私泄露风险" : "✓ 智能体仅能基于脱敏数据进行计算，符合《个人信息保护法》"}
          </div>
        </div>

        {/* 数据流转记录 */}
        <div className="didayi-card p-6">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Boxes className="h-5 w-5 text-indigo-600" />
            <h2 className="text-lg font-bold text-slate-900">数据流转链路</h2>
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-600">
              {flows.length} 条流转
            </span>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-12 text-slate-400">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              加载流转记录...
            </div>
          ) : flows.length === 0 ? (
            <div className="py-8 text-center text-sm text-slate-400">
              暂无数据流转记录
            </div>
          ) : (
            <div className="space-y-3">
              {flows.map((flow) => (
                <div key={flow.id} className="rounded-xl border border-slate-200 overflow-hidden">
                  <button
                    onClick={() => setExpandedFlow(expandedFlow === flow.id ? null : flow.id)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                  >
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-100 text-indigo-700">
                      <Database className="h-4 w-4" />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-slate-900">
                        {agentLabel[flow.agent] || flow.agent} → {flow.data_type}
                      </div>
                      <div className="text-xs text-slate-500">
                        4 步流转：申请 → 合规 → 隐私计算 → 存证
                      </div>
                    </div>
                    <CheckCircle2 className="h-4 w-4 text-green-500" />
                  </button>

                  {expandedFlow === flow.id && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      className="border-t border-slate-100 bg-slate-50/50 p-4"
                    >
                      <div className="flex items-center gap-2 overflow-x-auto pb-2">
                        {flow.steps.map((step, i) => (
                          <div key={i} className="flex items-center gap-2 shrink-0">
                            <div className="flex flex-col items-center gap-1">
                              <div className={cn(
                                "flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold",
                                i === 0 ? "bg-blue-100 text-blue-700" :
                                i === 1 ? "bg-amber-100 text-amber-700" :
                                i === 2 ? "bg-cyan-100 text-cyan-700" :
                                "bg-purple-100 text-purple-700"
                              )}>
                                {i + 1}
                              </div>
                              <span className="text-[10px] font-medium text-slate-600 whitespace-nowrap">
                                {step.step}
                              </span>
                            </div>
                            {i < flow.steps.length - 1 && (
                              <ArrowRight className="h-4 w-4 text-slate-300 shrink-0" />
                            )}
                          </div>
                        ))}
                      </div>
                      <div className="mt-3 space-y-1.5">
                        {flow.steps.map((step, i) => (
                          <div key={i} className="rounded-lg bg-white px-3 py-2 text-xs">
                            <span className="font-medium text-slate-700">{step.actor}：</span>
                            <span className="text-slate-600">{step.detail}</span>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 区块链存证模拟 */}
        <div className="didayi-card p-6">
          <div className="mb-4 flex items-center gap-2">
            <Fingerprint className="h-5 w-5 text-purple-600" />
            <h2 className="text-lg font-bold text-slate-900">审计存证链（用户授权事件）</h2>
          </div>
          <p className="mb-4 text-xs text-slate-500">
            每次数据访问生成 SHA-256 哈希，串联成不可篡改的证据链。
            用户侧数据访问事件哈希串联；下方「平台真实事件流」展示联邦任务与数据交易的正式存证。
          </p>
          <div className="flex items-center gap-2 overflow-x-auto pb-2">
            {flows.slice(0, 6).map((flow, i) => (
              <div key={flow.id} className="flex items-center gap-2 shrink-0">
                <div className="flex flex-col items-center gap-1">
                  <div className="rounded-lg bg-purple-50 px-3 py-2 text-center">
                    <div className="font-mono text-[10px] text-purple-700">
                      0x{flow.id.slice(-6).toUpperCase()}…
                    </div>
                    <div className="mt-0.5 text-[9px] text-slate-400">区块 #{i + 1}</div>
                  </div>
                </div>
                {i < Math.min(flows.length, 6) - 1 && (
                  <ArrowRight className="h-3 w-3 text-slate-300 shrink-0" />
                )}
              </div>
            ))}
          </div>
        </div>

        {/* 平台真实事件流（联邦任务 + 数据交易存证） */}
        <RealAuditChain />

        {/* 政策契合说明 */}
        <div className="rounded-2xl border border-cyan-200 bg-gradient-to-r from-cyan-50 to-blue-50 p-5">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-cyan-600" />
            <div className="text-xs text-slate-700">
              <p className="mb-1 font-semibold text-slate-900">对齐浙江省医保行业可信数据空间战略</p>
              <p>
                浙江省医保行业可信数据空间（「1+3+N」框架）已被国家数据局确定为全国重点联系示范场景。
                瓯医数链通过隐私计算、联邦查询、区块链存证等技术，在保护个人隐私的前提下释放医疗数据价值，
                完美契合「数据要素×医疗健康」国家战略。
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


/** 平台真实事件流：联邦任务 + 数据交易的审计存证链（非模拟，直连数据库） */
function RealAuditChain() {
  const [events, setEvents] = useState<
    Array<{ kind: string; label: string; status: string; event_hash: string | null; prev_hash: string | null; ts: string | null }>
  >([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const [jobs, txs] = await Promise.all([listFederationJobs(8), listDataTransactions(8)]);
      const merged = [
        ...(jobs ?? []).map((j) => ({
          kind: "联邦任务",
          label: `${j.rounds}轮${j.dp_sigma > 0 ? ` · DP σ=${j.dp_sigma}` : ""}`,
          status: j.status,
          event_hash: j.event_hash,
          prev_hash: j.prev_hash,
          ts: j.created_at,
        })),
        ...(txs ?? []).map((t) => ({
          kind: "数据交易",
          label: `${t.product_name} · ¥${t.amount.toLocaleString()}`,
          status: t.status,
          event_hash: t.event_hash,
          prev_hash: t.prev_hash,
          ts: t.created_at,
        })),
      ].sort((a, b) => (a.ts ?? "").localeCompare(b.ts ?? ""));
      setEvents(merged);
      setLoading(false);
    })();
  }, []);

  return (
    <div className="didayi-card p-6">
      <div className="mb-4 flex items-center gap-2">
        <Fingerprint className="h-5 w-5 text-cyan-600" />
        <h2 className="text-lg font-bold text-slate-900">平台真实事件流（联邦任务 × 数据交易）</h2>
        <span className="rounded-full bg-cyan-50 px-2 py-0.5 text-xs font-medium text-cyan-600">
          直连平台数据库 · 非模拟
        </span>
      </div>
      <p className="mb-4 text-xs text-slate-500">
        每个联邦任务与数据交易的摘要经 SHA-256 与前一事件哈希串联成链——任何篡改都会导致链条断裂，监管方可一键校验。
      </p>
      {loading ? (
        <div className="py-6 text-center text-xs text-slate-400">加载真实事件中…</div>
      ) : events.length === 0 ? (
        <div className="rounded-xl border border-dashed border-sky-200 p-5 text-center text-xs text-slate-400">
          暂无事件：请先在「联邦协作网络」发起训练任务，或在「数据要素市场」完成一笔授权交易
        </div>
      ) : (
        <div className="space-y-2">
          {events.map((e, i) => (
            <div key={`${e.event_hash}-${i}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-sky-50 bg-sky-50/40 px-3 py-2 text-xs">
              <span className="rounded-md bg-white px-1.5 py-0.5 font-medium text-slate-600 shadow-sm">{e.kind}</span>
              <span className="font-semibold text-slate-700">{e.label}</span>
              <span className="rounded-md bg-emerald-50 px-1.5 py-0.5 text-emerald-600">{e.status}</span>
              <span className="ml-auto font-mono text-[10px] text-slate-400">
                {(e.prev_hash ?? "").slice(0, 10)}… → {(e.event_hash ?? "").slice(0, 12)}…
              </span>
              <span className="w-full text-right font-mono text-[9px] text-slate-300">
                {e.ts ? new Date(e.ts).toLocaleString("zh-CN") : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
