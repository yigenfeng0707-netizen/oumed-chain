"use client";

/**
 * AI 病历治理（瓯医数链 · 数据供给侧）
 *
 * 演示动线：粘贴非结构化入院记录 → 一键治理 →
 *  ① PHI 脱敏对比（原文 vs 掩码后，敏感实体高亮）
 *  ② 本地大模型结构化（qwen3:4b 院内网推理，数据不出院）
 * 治理产物即"可流通数据产品"的原料。
 */

import { useState } from "react";
import { motion } from "framer-motion";
import {
  FileText,
  Loader2,
  ShieldCheck,
  Eye,
  EyeOff,
  Braces,
  ArrowRight,
} from "lucide-react";
import { BrandedPageHeader } from "@/components/branded-page-header";
import { governNote, type GovernResult } from "@/lib/api";
import { cn } from "@/lib/utils";

const SAMPLE_NOTE = `患者张阿姨，65岁，女，因反复胸闷气促3天入院。入院诊断：慢性心力衰竭急性加重，2型糖尿病，高血压3级。血压 158/92mmHg，心率 88次/分。既往高血压病史10年。联系方式 13812345678，身份证 330302196503124421，住院号 ZY2026088。予以呋塞米注射液 40mg 静推，美托洛尔缓释片 23.75mg 口服。`;

export default function GovernancePage() {
  const [text, setText] = useState(SAMPLE_NOTE);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GovernResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (text.trim().length < 5) return;
    setLoading(true);
    setError(null);
    try {
      const data = await governNote(text, true);
      if (data) setResult(data);
      else setError("后端服务不可用，请确认已启动（端口 8100）");
    } finally {
      setLoading(false);
    }
  }

  const structured = result?.structured;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6">
      <BrandedPageHeader
        title="AI 病历治理"
        description="非结构化病历 → PHI 脱敏 → 标准化数据集。治理全程院内网完成（本地大模型），脱敏产物才是可流通的数据原料"
        badge="数据供给侧"
      />

      {/* 输入区 */}
      <section className="rounded-2xl border border-sky-100 bg-white/90 p-5 shadow-sm">
        <div className="mb-2 flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
            <FileText className="h-4 w-4 text-cyan-600" />
            非结构化入院记录
          </label>
          <button
            onClick={() => setText(SAMPLE_NOTE)}
            className="text-xs text-cyan-600 hover:underline"
          >
            填入示例病历
          </button>
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          className="w-full resize-y rounded-xl border border-sky-200 bg-white p-3 text-sm leading-6 text-slate-700 outline-none focus:border-cyan-400"
          placeholder="粘贴病历原文…"
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={run}
            disabled={loading || text.trim().length < 5}
            className={cn(
              "flex items-center gap-2 rounded-xl bg-gradient-to-r from-cyan-500 to-sky-600 px-5 py-2 text-sm font-semibold text-white shadow-lg shadow-cyan-500/25 transition-all",
              (loading || text.trim().length < 5) && "cursor-not-allowed opacity-60"
            )}
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
            {loading ? "治理中（本地推理约10-60秒）…" : "一键治理"}
          </button>
          <span className="text-xs text-slate-400">
            流水线：PHI 脱敏(规则) → 结构化(本地 qwen3:4b) → 可流通数据原料
          </span>
        </div>
        {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
      </section>

      {result && (
        <>
          {/* 脱敏对比 */}
          <motion.section
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-2xl border border-sky-100 bg-white/90 p-5 shadow-sm"
          >
            <div className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-800">
              <EyeOff className="h-4 w-4 text-cyan-600" />
              第一步 · PHI 脱敏
              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-600">
                识别敏感实体 {result.deid.entity_count} 处
              </span>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                <div className="mb-1.5 flex items-center gap-1 text-xs font-medium text-slate-500">
                  <Eye className="h-3.5 w-3.5" /> 原文（含敏感信息，禁止出院）
                </div>
                <p className="text-xs leading-6 text-slate-500">{text}</p>
              </div>
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 p-3">
                <div className="mb-1.5 flex items-center gap-1 text-xs font-medium text-emerald-600">
                  <EyeOff className="h-3.5 w-3.5" /> 脱敏后（可进入治理与流通环节）
                </div>
                <p className="text-xs leading-6 text-slate-700">{result.deid.masked_text}</p>
              </div>
            </div>
          </motion.section>

          {/* 结构化 */}
          <motion.section
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-2xl border border-sky-100 bg-white/90 p-5 shadow-sm"
          >
            <div className="mb-3 flex flex-wrap items-center gap-2 text-sm font-bold text-slate-800">
              <Braces className="h-4 w-4 text-cyan-600" />
              第二步 · 结构化数据集
              {structured?.extractor?.startsWith("llm") ? (
                <span className="rounded-full bg-cyan-50 px-2 py-0.5 text-xs font-medium text-cyan-600">
                  本地大模型 qwen3:4b · 院内网推理
                </span>
              ) : (
                <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-600">
                  规则引擎兜底（本地模型未响应）
                </span>
              )}
              <ArrowRight className="h-3.5 w-3.5 text-slate-300" />
              <span className="text-xs font-normal text-slate-400">产物将进入数据产品目录</span>
            </div>
            <div className="grid gap-3 text-xs md:grid-cols-2">
              <div className="space-y-2 rounded-xl border border-sky-100 bg-sky-50/40 p-3">
                <div className="font-semibold text-slate-600">患者概况</div>
                <div className="text-slate-700">
                  {structured?.patient?.age ?? "—"} 岁 / {structured?.patient?.sex ?? "—"}
                </div>
                <div className="font-semibold text-slate-600">主诉</div>
                <div className="text-slate-700">{structured?.chief_complaint ?? "—"}</div>
                <div className="font-semibold text-slate-600">生命体征</div>
                <div className="text-slate-700">
                  血压 {structured?.vitals?.bp ?? "—"} · 心率 {structured?.vitals?.heart_rate ?? "—"}
                </div>
              </div>
              <div className="space-y-2 rounded-xl border border-sky-100 bg-sky-50/40 p-3">
                <div className="font-semibold text-slate-600">诊断</div>
                <div className="flex flex-wrap gap-1">
                  {(structured?.diagnoses ?? []).map((d, i) => (
                    <span key={i} className="rounded-lg bg-white px-2 py-1 text-slate-700 shadow-sm">
                      {d}
                    </span>
                  ))}
                </div>
                <div className="font-semibold text-slate-600">用药</div>
                <div className="flex flex-wrap gap-1">
                  {(structured?.medications ?? []).map((m, i) => (
                    <span key={i} className="rounded-lg bg-white px-2 py-1 text-slate-700 shadow-sm">
                      {m.name} {m.dose}
                    </span>
                  ))}
                </div>
                <div className="font-semibold text-slate-600">既往史</div>
                <div className="text-slate-700">
                  {(structured?.history ?? []).join("；") || "—"}
                </div>
              </div>
            </div>
          </motion.section>
        </>
      )}
    </div>
  );
}
