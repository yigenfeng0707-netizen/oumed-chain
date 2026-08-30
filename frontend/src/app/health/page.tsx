"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Heart,
  AlertCircle,
  Pill,
  TrendingDown,
  Activity,
  Apple,
  Footprints,
  Droplets,
  Moon,
  ChevronRight,
  Loader2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { motion } from "framer-motion";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { RadarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { getHealthProfile } from "@/lib/api";
import { useUser } from "@/lib/user-context";
import type { HealthProfile } from "@/lib/mock-data";
import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { DidaYiLogo } from "@/components/didayi-logo";

echarts.use([RadarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Apple,
  Footprints,
  Droplets,
  Moon,
  Activity,
};

function HealthScoreRing({ score }: { score: number }) {
  const size = 160;
  const strokeWidth = 10;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;

  const getColor = (s: number) => {
    if (s >= 80) return "#16b8c8";
    if (s >= 60) return "#f59e0b";
    return "#ff7a59";
  };

  const color = getColor(score);
  const label = score >= 80 ? "优秀" : score >= 60 ? "良好" : "需改善";

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#e9f7fb"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="transition-all duration-1000"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-4xl font-bold" style={{ color }}>{score}</span>
        <span className="text-sm font-medium" style={{ color }}>{label}</span>
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <Card className="didayi-card">
      <CardContent className="p-6">
        <div className="flex items-center justify-center h-32">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </CardContent>
    </Card>
  );
}

export default function HealthPage() {
  const [data, setData] = useState<HealthProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const { currentUser, userId } = useUser();

  useEffect(() => {
    setLoading(true);
    getHealthProfile(userId).then((result) => {
      setData(result);
      setLoading(false);
    });
  }, [userId]);

  const radarData = data?.radar_data || [];
  const alerts = data?.alerts || [];
  const medications = data?.medications || [];
  const trendData = data?.trend_data || [];
  const suggestions = data?.suggestions || [];

  const radarOption = {
    tooltip: {},
    radar: {
      indicator: radarData.map((d) => ({ name: d.name, max: 100 })),
      shape: "circle" as const,
      splitNumber: 4,
      axisName: { color: "#6b7280", fontSize: 12 },
      splitLine: { lineStyle: { color: "#e5e7eb" } },
      splitArea: { areaStyle: { color: ["#f9fafb", "#ffffff", "#f9fafb", "#ffffff"] } },
      axisLine: { lineStyle: { color: "#e5e7eb" } },
    },
    series: [
      {
        type: "radar",
        data: [
          {
            value: radarData.map((d) => d.value),
            name: "当前评分",
            areaStyle: { color: "rgba(25, 190, 210, 0.16)" },
            lineStyle: { color: "#19bed2", width: 2 },
            itemStyle: { color: "#19bed2" },
          },
          {
            value: radarData.map((d) => d.target),
            name: "目标评分",
            areaStyle: { color: "rgba(255, 122, 89, 0.06)" },
            lineStyle: { color: "#ff7a59", width: 2, type: "dashed" as const },
            itemStyle: { color: "#ff7a59" },
          },
        ],
      },
    ],
  };

  const trendOption = {
    tooltip: {
      trigger: "axis" as const,
      backgroundColor: "rgba(255,255,255,0.95)",
      borderColor: "#e5e7eb",
      textStyle: { color: "#1f2937", fontSize: 12 },
    },
    grid: { top: 20, right: 20, bottom: 30, left: 40 },
    xAxis: {
      type: "category" as const,
      data: trendData.map((d) => d.month),
      axisLine: { lineStyle: { color: "#e5e7eb" } },
      axisLabel: { color: "#9ca3af", fontSize: 11 },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value" as const,
      min: 50,
      max: 100,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "#f3f4f6", type: "dashed" as const } },
      axisLabel: { color: "#9ca3af", fontSize: 11 },
    },
    series: [
      {
        type: "line",
        data: trendData.map((d) => d.score),
        smooth: true,
        symbol: "circle",
        symbolSize: 8,
        lineStyle: { color: "#19bed2", width: 3 },
        itemStyle: { color: "#19bed2", borderColor: "#fff", borderWidth: 2 },
        areaStyle: {
          color: "rgba(25, 190, 210, 0.1)",
        },
        markLine: {
          silent: true,
          lineStyle: { color: "#ef4444", type: "dashed" as const },
          data: [{ yAxis: 60, label: { formatter: "警戒线", color: "#ef4444", fontSize: 10 } }],
        },
      },
    ],
  };

  if (loading) {
    return (
      <div className="didayi-page space-y-6">
        <motion.div {...fadeIn}>
          <h1 className="text-2xl font-bold text-slate-800">健康画像</h1>
          <p className="text-sm text-slate-500">正在为 {currentUser.name} 汇总健康数据…</p>
        </motion.div>
        <div className="grid gap-5 lg:grid-cols-3">
          <SkeletonCard />
          <div className="lg:col-span-2"><SkeletonCard /></div>
        </div>
      </div>
    );
  }

  const healthScore = data?.health_score || 72;

  return (
    <div className="didayi-page space-y-5">
      {/* Page Header */}
      <motion.div {...fadeIn}>
        <div className="relative overflow-hidden rounded-3xl border border-sky-100 bg-[linear-gradient(120deg,#e9faff_0%,#f6fdff_55%,#fff7f3_100%)] px-6 py-5 shadow-[0_14px_36px_rgba(30,134,185,.09)] lg:px-8">
          <div className="absolute -right-10 -top-20 h-52 w-52 rounded-full bg-cyan-300/20 blur-3xl" />
          <div className="relative flex flex-col justify-between gap-5 lg:flex-row lg:items-center">
            <div className="flex items-start gap-4">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-sky-500 text-white shadow-lg shadow-cyan-500/20">
                <Sparkles className="h-6 w-6" />
              </span>
              <div>
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <h1 className="text-2xl font-bold tracking-tight text-slate-800">健康画像</h1>
                  <span className="rounded-full border border-cyan-200 bg-white/75 px-2.5 py-1 text-[11px] font-semibold text-cyan-700">智能评估</span>
                </div>
                <p className="text-sm leading-6 text-slate-500">
                  基于 <span className="font-semibold text-slate-700">{currentUser.name}</span> 的就医、用药与健康记录，持续呈现个人健康趋势。
                </p>
              </div>
            </div>
            <div className="flex items-center gap-4 rounded-2xl border border-white/80 bg-white/70 px-4 py-3 shadow-sm backdrop-blur-sm">
              <DidaYiLogo />
              <div className="hidden border-l border-sky-100 pl-4 sm:block"><ApiStatusIndicator /></div>
            </div>
          </div>
        </div>
      </motion.div>

      {/* Top Section: Health Score + Radar Chart */}
      <div className="grid gap-5 lg:grid-cols-3">
        {/* Health Score Card */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.1 }}>
          <Card className="didayi-card h-full">
            <CardContent className="p-6 flex flex-col items-center justify-center h-full">
              <HealthScoreRing score={healthScore} />
              <div className="mt-4 text-center">
                <h3 className="text-lg font-semibold text-foreground">综合健康评分</h3>
                <p className="text-sm text-muted-foreground mt-1">基于医保消费、用药、就医数据综合评估</p>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-3 w-full">
                <div className="text-center p-2 rounded-lg bg-red-50">
                  <p className="text-lg font-bold text-red-600">{alerts.filter((a) => a.severity === "high").length}</p>
                  <p className="text-xs text-red-500">高风险</p>
                </div>
                <div className="text-center p-2 rounded-lg bg-yellow-50">
                  <p className="text-lg font-bold text-yellow-600">{alerts.filter((a) => a.severity === "medium").length}</p>
                  <p className="text-xs text-yellow-500">中风险</p>
                </div>
                <div className="text-center p-2 rounded-lg bg-green-50">
                  <p className="text-lg font-bold text-green-600">{alerts.filter((a) => a.severity === "low").length}</p>
                  <p className="text-xs text-green-500">低风险</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </motion.div>

        {/* Radar Chart */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.15 }} className="lg:col-span-2">
          <Card className="didayi-card h-full">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <Activity className="h-4 w-4 text-cyan-500" />
                  五维健康评估
                </CardTitle>
                <div className="flex items-center gap-4 text-xs">
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full bg-cyan-500" />
                    当前评分
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full bg-[#ff7a59]" />
                    目标评分
                  </span>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <ReactEChartsCore
                echarts={echarts}
                option={radarOption}
                style={{ height: 300 }}
                notMerge
              />
            </CardContent>
          </Card>
        </motion.div>
      </div>

      {/* Alert Cards */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.2 }}>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {alerts.map((alert, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.3, delay: 0.25 + i * 0.1 }}
            >
              <Card
                className={`didayi-card border-l-4 ${
                  alert.severity === "high"
                    ? "border-l-red-500"
                    : alert.severity === "medium"
                    ? "border-l-yellow-500"
                    : "border-l-green-500"
                }`}
              >
                <CardContent className="p-4">
                  <div className="flex items-start gap-3">
                    <span className="text-xl">{alert.icon}</span>
                    <div className="flex-1 min-w-0">
                      <h4 className="text-sm font-semibold text-foreground">{alert.title}</h4>
                      <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{alert.desc}</p>
                      <Button variant="link" className="h-auto p-0 mt-2 text-xs" size="sm">
                        {alert.action} <ChevronRight className="h-3 w-3 ml-0.5" />
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      </motion.div>

      {/* Medication Review + Health Trend */}
      <div className="grid gap-5 lg:grid-cols-2">
        {/* Medication Review */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.35 }}>
          <Card className="didayi-card h-full">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Pill className="h-4 w-4 text-cyan-500" />
                用药审查
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {medications.map((med, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between p-3 rounded-lg bg-gray-50/80 hover:bg-gray-100/80 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white shadow-sm">
                        <Pill className="h-4 w-4 text-cyan-500" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">{med.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {med.dosage} · {med.frequency}
                        </p>
                      </div>
                    </div>
                    <Badge variant="secondary" className={`text-xs ${med.statusColor}`}>
                      {med.status}
                    </Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>

        {/* Health Trend Chart */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.4 }}>
          <Card className="didayi-card h-full">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <TrendingDown className="h-4 w-4 text-cyan-500" />
                  健康趋势
                </CardTitle>
                <Badge variant="secondary" className="text-xs">近6个月</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <ReactEChartsCore
                echarts={echarts}
                option={trendOption}
                style={{ height: 260 }}
                notMerge
              />
            </CardContent>
          </Card>
        </motion.div>
      </div>

      {/* Health Improvement Suggestions */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.45 }}>
        <Card className="didayi-card">
          <CardHeader className="pb-2">
            <CardTitle className="text-base font-semibold flex items-center gap-2">
              <Heart className="h-4 w-4 text-[#ff7a59]" />
              个性化健康改善建议
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
              {suggestions.map((s, i) => {
                const IconComp = iconMap[s.icon];
                return (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3, delay: 0.5 + i * 0.05 }}
                    className="p-4 rounded-xl bg-gray-50/80 hover:bg-gray-100/80 transition-colors cursor-pointer group"
                  >
                    <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${s.color} mb-3`}>
                      {IconComp ? <IconComp className="h-5 w-5" /> : <Activity className="h-5 w-5" />}
                    </div>
                    <h4 className="text-sm font-semibold text-foreground mb-1">{s.title}</h4>
                    <p className="text-xs text-muted-foreground leading-relaxed">{s.desc || s.description}</p>
                  </motion.div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      </motion.div>
      <p className="flex items-start gap-2 px-1 text-xs leading-5 text-slate-400">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cyan-500" />
        画像用于整理和提示已有健康信息，不替代医生诊断；如有不适或指标异常，请及时咨询专业医疗人员。
      </p>
    </div>
  );
}
