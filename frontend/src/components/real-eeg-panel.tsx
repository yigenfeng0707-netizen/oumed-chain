"use client";

/**
 * 真实公开 EEG 数据集面板（科技医学风格）
 *
 * 展示 scripts/ingest_real_eeg.py 接入的真实脑电数据集（PhysioNet eegmmidb 等）：
 * - 数据源统计徽章（真实/合成标记）
 * - 记录卡片网格：五维健康指标迷你仪表 + 元信息
 * - 点击展开详情：五频段功率谱 + 预警 + 医保政策联动
 *
 * 视觉：深色监护仪美学（青色荧光、网格纹理、等宽数字）
 */
import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Database,
  Loader2,
  ChevronDown,
  Activity,
  AlertTriangle,
  ShieldCheck,
  FlaskConical,
  Waves,
  CircleDot,
} from "lucide-react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import {
  getRealEEGSessions,
  getRealEEGDetail,
  type RealEEGListResponse,
  type RealEEGDetail,
} from "@/lib/api";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

// 频段元信息（与主页面一致）
const BAND_META: Record<string, { label: string; color: string }> = {
  delta: { label: "δ", color: "#22d3ee" },
  theta: { label: "θ", color: "#34d399" },
  alpha: { label: "α", color: "#818cf8" },
  beta: { label: "β", color: "#fbbf24" },
  gamma: { label: "γ", color: "#fb7185" },
};

const SOURCE_META: Record<string, { label: string; real: boolean }> = {
  physionet: { label: "PhysioNet eegmmidb", real: true },
  eegmmidb: { label: "PhysioNet eegmmidb", real: true },
  eegemotions27: { label: "EEGEmotions-27 情绪", real: true },
  local: { label: "本地导入", real: true },
  demo: { label: "合成验证", real: false },
};

function isRealSource(source: string): boolean {
  return SOURCE_META[source]?.real ?? true;
}

/** 迷你指标条（水平进度，监护仪风格） */
function MiniMetric({
  label,
  value,
  reverse = false,
}: {
  label: string;
  value: number | undefined;
  reverse?: boolean;
}) {
  if (value === undefined || value === null) return null;
  const v = Math.round(value);
  const danger = reverse ? v >= 60 : v >= 70;
  const warn = reverse ? v >= 40 && v < 60 : v >= 40 && v < 70;
  const color = danger ? "#fb7185" : warn ? "#fbbf24" : "#34d399";
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 shrink-0 text-[10px] text-slate-400">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-700/60">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${v}%`, backgroundColor: color, boxShadow: `0 0 6px ${color}` }}
        />
      </div>
      <span className="w-8 shrink-0 text-right font-mono text-[11px] tabular-nums" style={{ color }}>
        {v}
      </span>
    </div>
  );
}

export function RealEEGPanel() {
  const [list, setList] = useState<RealEEGListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<RealEEGDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let mounted = true;
    getRealEEGSessions(undefined, 30).then((data) => {
      if (!mounted) return;
      setList(data);
      setLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, []);

  const toggleDetail = useCallback(
    async (recordId: string) => {
      if (expanded === recordId) {
        setExpanded(null);
        setDetail(null);
        return;
      }
      setExpanded(recordId);
      setDetail(null);
      setDetailLoading(true);
      const d = await getRealEEGDetail(recordId);
      setDetail(d);
      setDetailLoading(false);
    },
    [expanded],
  );

  if (loading) {
    return (
      <div className="flex h-32 items-center justify-center rounded-xl border border-cyan-500/20 bg-slate-900/80">
        <Loader2 className="h-5 w-5 animate-spin text-cyan-400" />
        <span className="ml-2 text-sm text-slate-400">加载真实数据集…</span>
      </div>
    );
  }

  if (!list || list.sessions.length === 0) return null;

  const realCount = list.sessions.filter((s) => isRealSource(s.source)).length;
  const bandOption = detail
    ? {
        animation: true,
        tooltip: { trigger: "axis" as const },
        grid: { top: 20, right: 10, bottom: 24, left: 40 },
        xAxis: {
          type: "category" as const,
          data: Object.keys(detail.avg_band_powers || {}).map((b) => BAND_META[b]?.label || b),
          axisLabel: { fontSize: 11, color: "#94a3b8" },
          axisLine: { lineStyle: { color: "#334155" } },
        },
        yAxis: {
          type: "value" as const,
          axisLabel: { fontSize: 10, color: "#64748b" },
          splitLine: { lineStyle: { color: "#1e293b" } },
        },
        series: [
          {
            type: "bar",
            barWidth: "45%",
            data: Object.entries(detail.avg_band_powers || {}).map(([b, v]) => ({
              value: Number(Number(v).toFixed(3)),
              itemStyle: {
                color: BAND_META[b]?.color || "#22d3ee",
                borderRadius: [3, 3, 0, 0],
              },
            })),
          },
        ],
      }
    : null;

  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45 }}
      className="relative overflow-hidden rounded-xl border border-cyan-500/25 bg-slate-900 text-slate-100 shadow-[0_0_40px_-12px_rgba(34,211,238,0.35)]"
    >
      {/* 网格纹理背景 */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(34,211,238,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(34,211,238,0.06) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }}
      />
      {/* 顶部荧光条 */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/70 to-transparent" />

      {/* 标题栏 */}
      <div className="relative flex flex-wrap items-center justify-between gap-3 border-b border-slate-700/60 px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-cyan-500/15 ring-1 ring-cyan-400/40">
            <Database className="h-4 w-4 text-cyan-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold tracking-wide">真实公开数据集 · 脑电科研验证</h3>
              {realCount > 0 && (
                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-400/40 bg-emerald-400/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                  <CircleDot className="h-3 w-3 animate-pulse" />
                  REAL DATA
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              PhysioNet eegmmidb（ODC-By 许可）· 纯 Python EDF 解析 → 五维指标 → 医保政策联动
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px] text-slate-400">
          <span className="rounded border border-slate-600/60 bg-slate-800/60 px-2 py-1">
            {list.total} 条评估
          </span>
          {Object.entries(list.datasets).map(([k, v]) => (
            <span
              key={k}
              className={`rounded border px-2 py-1 ${
                isRealSource(k)
                  ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-300"
                  : "border-slate-600/60 bg-slate-800/60"
              }`}
            >
              {SOURCE_META[k]?.label || k} × {v.count}
            </span>
          ))}
        </div>
      </div>

      {/* 记录卡片网格 */}
      <div className="relative grid gap-3 p-5 sm:grid-cols-2 xl:grid-cols-3">
        {list.sessions.map((s, i) => {
          const meta = SOURCE_META[s.source] || { label: s.source, real: true };
          const isOpen = expanded === s.record_id;
          return (
            <motion.button
              key={s.record_id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.05 * i }}
              onClick={() => toggleDetail(s.record_id)}
              className={`group relative overflow-hidden rounded-lg border p-4 text-left backdrop-blur-sm transition-all ${
                isOpen
                  ? "border-cyan-400/60 bg-slate-800/90 shadow-[0_0_20px_-6px_rgba(34,211,238,0.5)]"
                  : "border-slate-700/60 bg-slate-800/50 hover:border-cyan-500/40 hover:bg-slate-800/80"
              }`}
            >
              {/* 卡片顶部：ID + 状态 */}
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm font-semibold text-cyan-300">{s.record_id}</span>
                  {meta.real && (
                    <span className="rounded bg-emerald-400/15 px-1.5 py-0.5 text-[9px] font-bold tracking-wider text-emerald-300">
                      REAL
                    </span>
                  )}
                </div>
                <span
                  className="inline-flex items-center gap-1 text-xs"
                  style={{
                    color:
                      s.mental_state === "stressed"
                        ? "#fb7185"
                        : s.mental_state === "focused"
                          ? "#fbbf24"
                          : "#34d399",
                  }}
                >
                  <Waves className="h-3.5 w-3.5" />
                  {s.mental_state_label || s.mental_state}
                </span>
              </div>

              {/* 元信息行 */}
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-slate-500">
                <span>{meta.label}</span>
                {s.origin_sample_rate ? <span>{s.origin_sample_rate} Hz</span> : null}
                <span>{s.duration_seconds}s</span>
                {s.dataset_meta?.paradigm ? (
                  <span className="text-slate-400">{s.dataset_meta.paradigm}</span>
                ) : null}
              </div>

              {/* 迷你指标（四维核心） */}
              <div className="mt-3 space-y-1.5">
                <MiniMetric label="压力" value={s.metrics?.stress_index as number} />
                <MiniMetric label="注意力" value={s.metrics?.attention_index as number} />
                <MiniMetric label="睡眠" value={s.metrics?.sleep_quality as number} reverse />
                <MiniMetric label="认知" value={s.metrics?.cognitive_load as number} />
              </div>

              {/* 底部：预警计数 + 展开提示 */}
              <div className="mt-3 flex items-center justify-between border-t border-slate-700/50 pt-2">
                <span
                  className={`inline-flex items-center gap-1 text-[11px] ${
                    s.alerts_count > 0 ? "text-amber-300" : "text-slate-500"
                  }`}
                >
                  <AlertTriangle className="h-3 w-3" />
                  {s.alerts_count} 项预警
                </span>
                <span className="inline-flex items-center gap-0.5 text-[10px] text-slate-500 group-hover:text-cyan-300">
                  详情
                  <ChevronDown
                    className={`h-3.5 w-3.5 transition-transform ${isOpen ? "rotate-180" : ""}`}
                  />
                </span>
              </div>
            </motion.button>
          );
        })}
      </div>

      {/* 详情抽屉 */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3 }}
            className="relative overflow-hidden border-t border-cyan-500/20 bg-slate-950/70"
          >
            {detailLoading && (
              <div className="flex items-center justify-center gap-2 py-10">
                <Loader2 className="h-4 w-4 animate-spin text-cyan-400" />
                <span className="text-sm text-slate-400">解析 {expanded} 数据…</span>
              </div>
            )}
            {detail && (
              <div className="grid gap-5 p-5 lg:grid-cols-2">
                {/* 左：频段功率谱 */}
                <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-4">
                  <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-cyan-300">
                    <Activity className="h-4 w-4" /> 五频段功率谱（Welch PSD）
                  </p>
                  {bandOption && (
                    <ReactEChartsCore
                      echarts={echarts}
                      option={bandOption}
                      style={{ height: 220 }}
                      notMerge
                    />
                  )}
                  <p className="mt-1 font-mono text-[10px] text-slate-500">
                    原始通道 {detail.origin_channels?.length || detail.channels.length} 个 ·
                    映射至 Muse 4 通道布局
                  </p>
                </div>

                {/* 右：预警 + 政策联动 */}
                <div className="space-y-4">
                  <div className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-4">
                    <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-amber-300">
                      <AlertTriangle className="h-4 w-4" /> 脑电预警（{detail.alerts?.length || 0}）
                    </p>
                    <div className="max-h-36 space-y-1.5 overflow-y-auto pr-1">
                      {(detail.alerts || []).map((a, i) => (
                        <div
                          key={i}
                          className="rounded border-l-2 border-amber-400/70 bg-slate-800/60 px-3 py-1.5"
                        >
                          <p className="text-xs font-medium text-slate-200">{a.title}</p>
                          <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">
                            {a.description || a.desc}
                          </p>
                        </div>
                      ))}
                      {(!detail.alerts || detail.alerts.length === 0) && (
                        <p className="py-3 text-center text-xs text-slate-500">无预警</p>
                      )}
                    </div>
                  </div>

                  <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-4">
                    <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-indigo-300">
                      <ShieldCheck className="h-4 w-4" /> 医保政策联动
                    </p>
                    <div className="max-h-40 space-y-2 overflow-y-auto pr-1">
                      {(detail.policy_links || []).map((l, i) => (
                        <div
                          key={i}
                          className="rounded border border-indigo-500/20 bg-slate-900/70 px-3 py-2"
                        >
                          <p className="text-xs font-medium text-slate-200">{l.title}</p>
                          <p className="mt-0.5 text-[11px] text-slate-400">{l.suggestion}</p>
                        </div>
                      ))}
                      {(!detail.policy_links || detail.policy_links.length === 0) && (
                        <p className="py-3 text-center text-xs text-slate-500">
                          该记录无政策联动触发
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* 底部免责声明 */}
      <div className="relative border-t border-slate-700/60 px-5 py-2.5">
        <p className="flex items-center gap-1.5 text-[10px] text-slate-500">
          <FlaskConical className="h-3 w-3" />
          {list.note || "真实公开数据集脑电，指标仅供科研演示，不构成医疗诊断。"}
        </p>
      </div>
    </motion.section>
  );
}
