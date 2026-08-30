"use client";

/**
 * 联邦学习协作网络（瓯医数链底座 · 核心演示页）
 *
 * 三家模拟医院数据不出院联合建模：
 * - 医院数据全景（联邦统计口径）
 * - 发起联邦训练任务（可开差分隐私），实时查看 AUC 曲线与逐院公平性
 * - 标准基准实验：本地 vs 联邦 vs DP 分档 vs 集中训练上界
 * - 每个任务结果接入审计存证链（hash 串联，防篡改）
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Building2,
  Play,
  Loader2,
  ShieldCheck,
  Link2,
  TrendingUp,
  Users,
  Database,
} from "lucide-react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { BrandedPageHeader } from "@/components/branded-page-header";
import {
  getFederationOverview,
  createFederationJob,
  listFederationJobs,
  getFederationBenchmark,
  type FederationOverview,
  type FederationJobDetail,
  type FederationJobSummary,
  type FederationBenchmark,
} from "@/lib/api";
import { cn } from "@/lib/utils";

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

const DP_OPTIONS = [
  { value: 0, label: "关闭（性能优先）" },
  { value: 0.01, label: "σ=0.01 轻噪声（推荐）" },
  { value: 0.03, label: "σ=0.03 中噪声" },
  { value: 0.08, label: "σ=0.08 强噪声" },
];

export default function FederationPage() {
  const [overview, setOverview] = useState<FederationOverview | null>(null);
  const [jobs, setJobs] = useState<FederationJobSummary[]>([]);
  const [rounds, setRounds] = useState(12);
  const [epochs, setEpochs] = useState(3);
  const [sigma, setSigma] = useState<number>(0);
  const [running, setRunning] = useState(false);
  const [lastJob, setLastJob] = useState<FederationJobDetail | null>(null);
  const [bench, setBench] = useState<FederationBenchmark | null>(null);
  const [benchLoading, setBenchLoading] = useState(false);
  const benchRequested = useRef(false);

  const refreshJobs = useCallback(async () => {
    const data = await listFederationJobs();
    if (data) setJobs(data);
  }, []);

  useEffect(() => {
    getFederationOverview().then(setOverview);
    refreshJobs();
  }, [refreshJobs]);

  async function launchJob() {
    setRunning(true);
    setLastJob(null);
    try {
      const job = await createFederationJob({ rounds, local_epochs: epochs, dp_sigma: sigma });
      if (job) setLastJob(job);
      await refreshJobs();
    } finally {
      setRunning(false);
    }
  }

  async function loadBenchmark() {
    if (benchRequested.current) return;
    benchRequested.current = true;
    setBenchLoading(true);
    try {
      const data = await getFederationBenchmark();
      if (data) setBench(data);
    } finally {
      setBenchLoading(false);
    }
  }

  const curveOption = lastJob && {
    grid: { left: 50, right: 20, top: 30, bottom: 30 },
    tooltip: { trigger: "axis" as const },
    xAxis: {
      type: "category" as const,
      name: "联邦轮次",
      data: lastJob.result.auc_curve.map((_, i) => i),
    },
    yAxis: { type: "value" as const, min: 0.5, max: 1, name: "AUC" },
    series: [
      {
        name: "全局测试 AUC",
        type: "line" as const,
        data: lastJob.result.auc_curve,
        smooth: true,
        itemStyle: { color: "#0891b2" },
        areaStyle: { color: "rgba(8,145,178,.08)" },
      },
    ],
  };

  const perSiteOption = lastJob && {
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    tooltip: { trigger: "axis" as const },
    legend: { data: ["本院本地模型", "联邦模型"] },
    xAxis: {
      type: "category" as const,
      data: Object.keys(lastJob.result.per_site),
    },
    yAxis: { type: "value" as const, min: 0.6, max: 1, name: "本院测试 AUC" },
    series: [
      {
        name: "本院本地模型",
        type: "bar" as const,
        data: Object.values(lastJob.result.per_site).map((v) => v.local),
        itemStyle: { color: "#94a3b8", borderRadius: [6, 6, 0, 0] },
      },
      {
        name: "联邦模型",
        type: "bar" as const,
        data: Object.values(lastJob.result.per_site).map((v) => v.federated),
        itemStyle: { color: "#0891b2", borderRadius: [6, 6, 0, 0] },
      },
    ],
  };

  const benchGlobalOption = bench && {
    grid: { left: 60, right: 20, top: 40, bottom: 80 },
    tooltip: { trigger: "axis" as const },
    xAxis: {
      type: "category" as const,
      data: [
        ...Object.keys(bench.local_auc),
        "联邦 FedAvg",
        "集中训练上界",
      ],
      axisLabel: { rotate: 20, fontSize: 10 },
    },
    yAxis: { type: "value" as const, min: 0.6, max: 0.85, name: "全局 AUC" },
    series: [
      {
        name: "AUC",
        type: "bar" as const,
        data: [
          ...Object.values(bench.local_auc).map((v) => ({
            value: v,
            itemStyle: { color: "#94a3b8" },
          })),
          { value: bench.fed_auc, itemStyle: { color: "#0891b2" } },
          { value: bench.pooled_oracle_auc, itemStyle: { color: "#f59e0b" } },
        ],
        borderRadius: [6, 6, 0, 0],
        label: { show: true, position: "top" as const, fontSize: 10 },
      },
    ],
  };

  const benchDpOption = bench && {
    grid: { left: 60, right: 20, top: 40, bottom: 30 },
    tooltip: { trigger: "axis" as const },
    xAxis: {
      type: "category" as const,
      data: ["无隐私", "σ=0.01", "σ=0.03", "σ=0.08"],
    },
    yAxis: { type: "value" as const, min: 0.5, max: 0.8, name: "AUC" },
    series: [
      {
        name: "隐私-效用权衡",
        type: "line" as const,
        data: [bench.fed_auc, ...Object.values(bench.dp).map((v) => v.auc)],
        smooth: true,
        itemStyle: { color: "#7c3aed" },
        areaStyle: { color: "rgba(124,58,237,.08)" },
      },
    ],
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6">
      <BrandedPageHeader
        title="联邦学习协作网络"
        description="三家医院数据不出院，联合训练心衰再入院风险模型——「可用不可见、可控可计量」的医疗数据要素协作底座"
        badge="数据要素底座"
      />

      {/* 医院数据全景 */}
      <section className="grid gap-4 md:grid-cols-3">
        {(overview?.hospitals ?? []).map((h, i) => (
          <motion.div
            key={h.site}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.08 }}
            className="rounded-2xl border border-sky-100 bg-white/90 p-4 shadow-sm"
          >
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <Building2 className="h-4 w-4 text-cyan-600" />
              {h.site}
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs text-slate-500">
              <div>
                <Users className="mr-1 inline h-3.5 w-3.5" />
                样本 {h.total}
              </div>
              <div>
                <TrendingUp className="mr-1 inline h-3.5 w-3.5" />
                再入院率 {(h.prevalence * 100).toFixed(1)}%
              </div>
              <div>
                <Database className="mr-1 inline h-3.5 w-3.5" />
                平均年龄 {h.mean_age} 岁
              </div>
              <div>
                <ShieldCheck className="mr-1 inline h-3.5 w-3.5" />
                EF缺失 {(h.missing_ef * 100).toFixed(0)}%
              </div>
            </div>
          </motion.div>
        ))}
        {!overview && (
          <div className="col-span-3 rounded-2xl border border-dashed border-sky-200 bg-white/60 p-6 text-center text-sm text-slate-400">
            正在连接联邦网络…（请确认后端服务已启动）
          </div>
        )}
      </section>

      {/* 发起联邦任务 */}
      <section className="rounded-2xl border border-sky-100 bg-white/90 p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-end gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">联邦轮次</label>
            <input
              type="number"
              min={1}
              max={50}
              value={rounds}
              onChange={(e) => setRounds(Number(e.target.value))}
              className="w-24 rounded-lg border border-sky-200 px-3 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">本地Epoch</label>
            <input
              type="number"
              min={1}
              max={20}
              value={epochs}
              onChange={(e) => setEpochs(Number(e.target.value))}
              className="w-24 rounded-lg border border-sky-200 px-3 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">差分隐私</label>
            <select
              value={sigma}
              onChange={(e) => setSigma(Number(e.target.value))}
              className="rounded-lg border border-sky-200 px-3 py-1.5 text-sm"
            >
              {DP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={launchJob}
            disabled={running || !overview}
            className={cn(
              "flex items-center gap-2 rounded-xl bg-gradient-to-r from-cyan-500 to-sky-600 px-5 py-2 text-sm font-semibold text-white shadow-lg shadow-cyan-500/25 transition-all",
              (running || !overview) && "cursor-not-allowed opacity-60"
            )}
          >
            {running ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {running ? "联邦训练中…" : "发起联邦训练"}
          </button>
          <button
            onClick={loadBenchmark}
            disabled={benchLoading}
            className="ml-auto flex items-center gap-2 rounded-xl border border-sky-200 bg-white px-4 py-2 text-sm font-medium text-cyan-700 transition-all hover:bg-sky-50"
          >
            {benchLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <TrendingUp className="h-4 w-4" />
            )}
            {benchLoading ? "基准实验运行中（约1分钟）…" : bench ? "刷新标准基准" : "运行标准基准实验"}
          </button>
        </div>

        {/* 最近任务存证链 */}
        {jobs.length > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <Link2 className="h-3.5 w-3.5" />
            <span className="font-medium text-slate-500">审计存证链：</span>
            {jobs.slice(0, 5).reverse().map((j) => (
              <span
                key={j.id}
                className="rounded-md bg-slate-50 px-2 py-1 font-mono text-[10px]"
                title={`${j.rounds}轮 σ=${j.dp_sigma}`}
              >
                {j.event_hash?.slice(0, 10)}
              </span>
            ))}
          </div>
        )}

        {lastJob && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-4"
          >
            <div className="flex flex-wrap gap-3">
              <div className="rounded-xl bg-gradient-to-br from-cyan-500 to-sky-600 px-4 py-3 text-white">
                <div className="text-xs opacity-80">联邦模型全局 AUC</div>
                <div className="text-2xl font-bold">{lastJob.result.final_auc}</div>
              </div>
              <div className="rounded-xl border border-sky-100 bg-sky-50/60 px-4 py-3">
                <div className="text-xs text-slate-500">配置</div>
                <div className="text-sm font-semibold text-slate-700">
                  {lastJob.rounds} 轮 × {lastJob.local_epochs} epoch · DP σ={lastJob.dp_sigma}
                </div>
              </div>
              <div className="rounded-xl border border-sky-100 bg-sky-50/60 px-4 py-3">
                <div className="text-xs text-slate-500">耗时</div>
                <div className="text-sm font-semibold text-slate-700">
                  {lastJob.duration_ms} ms
                </div>
              </div>
              <div className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3">
                <div className="text-xs text-slate-500">存证哈希（防篡改）</div>
                <div className="truncate font-mono text-xs text-slate-600">
                  {lastJob.prev_hash?.slice(0, 16)}… → {lastJob.event_hash?.slice(0, 16)}…
                </div>
              </div>
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              {curveOption && (
                <ReactEChartsCore
                  echarts={echarts}
                  option={curveOption}
                  style={{ height: 260 }}
                  notMerge
                />
              )}
              {perSiteOption && (
                <ReactEChartsCore
                  echarts={echarts}
                  option={perSiteOption}
                  style={{ height: 260 }}
                  notMerge
                />
              )}
            </div>
          </motion.div>
        )}
      </section>

      {/* 标准基准实验 */}
      {bench && (
        <motion.section
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-2xl border border-sky-100 bg-white/90 p-5 shadow-sm"
        >
          <h2 className="mb-1 text-base font-bold text-slate-800">
            标准基准实验：数据要素协作的价值证明
          </h2>
          <p className="mb-4 text-xs text-slate-500">
            全局部署视角（混合人群）：联邦模型 {bench.fed_auc}，不低于任何单院模型，并达到
            「数据大池化」集中训练上界（{bench.pooled_oracle_auc}）——而现实中集中训练受合规限制不可行
          </p>
          <div className="grid gap-4 lg:grid-cols-2">
            {benchGlobalOption && (
              <ReactEChartsCore
                echarts={echarts}
                option={benchGlobalOption}
                style={{ height: 300 }}
                notMerge
              />
            )}
            {benchDpOption && (
              <ReactEChartsCore
                echarts={echarts}
                option={benchDpOption}
                style={{ height: 300 }}
                notMerge
              />
            )}
          </div>
        </motion.section>
      )}
    </div>
  );
}
