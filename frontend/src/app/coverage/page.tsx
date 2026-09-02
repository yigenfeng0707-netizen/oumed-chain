"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  Shield,
  TrendingUp,
  Wallet,
  Clock,
  Building2,
  Calendar,
  ArrowUpRight,
  ArrowDownRight,
  Activity,
  Loader2,
} from "lucide-react";
import { motion } from "framer-motion";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { getCoverageSummary } from "@/lib/api";
import { useUser } from "@/lib/user-context";
import type { CoverageSummary } from "@/lib/mock-data";
import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { BrandedPageHeader } from "@/components/branded-page-header";

echarts.use([BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

const months = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

// 下次缴费时间：次月 15 日（动态计算，避免演示数据过期）
function nextPaymentDate(): string {
  const d = new Date();
  d.setMonth(d.getMonth() + 1);
  d.setDate(15);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${d.getFullYear()}-${m}-15`;
}

function CircularProgress({ value, size = 80, strokeWidth = 6, color }: { value: number; size?: number; strokeWidth?: number; color: string }) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-muted/30"
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
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-lg font-bold" style={{ color }}>{value}%</span>
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <Card className="didayi-card">
      <CardContent className="p-6">
        <div className="flex items-center justify-center h-24">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </CardContent>
    </Card>
  );
}

export default function CoveragePage() {
  const [data, setData] = useState<CoverageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const { userId } = useUser();

  useEffect(() => {
    setLoading(true);
    getCoverageSummary(userId).then((result) => {
      setData(result);
      setLoading(false);
    });
  }, [userId]);

  const paymentData = data?.payment_history || [];
  const recentActivities = data?.recent_activities || [];

  const barChartOption = {
    tooltip: {
      trigger: "axis" as const,
      backgroundColor: "rgba(255,255,255,0.95)",
      borderColor: "#e5e7eb",
      textStyle: { color: "#1f2937", fontSize: 12 },
      formatter: (params: any) => {
        const p = params[0];
        return `${p.name}<br/>缴费金额：<b>¥${p.value}</b>`;
      },
    },
    grid: { top: 20, right: 20, bottom: 30, left: 50 },
    xAxis: {
      type: "category" as const,
      data: months,
      axisLine: { lineStyle: { color: "#e5e7eb" } },
      axisLabel: { color: "#9ca3af", fontSize: 11 },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value" as const,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "#f3f4f6", type: "dashed" as const } },
      axisLabel: { color: "#9ca3af", fontSize: 11, formatter: "¥{value}" },
    },
    series: [
      {
        type: "bar",
        data: paymentData,
        barWidth: "50%",
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
          color: "#19bed2",
        },
      },
    ],
  };

  if (loading) {
    return (
      <div className="didayi-page space-y-5">
        <motion.div {...fadeIn}>
          <h1 className="text-2xl font-bold text-foreground">权益全景</h1>
          <p className="text-sm text-muted-foreground">全面了解您的医保权益与保障范围</p>
        </motion.div>
        <SkeletonCard />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[1, 2, 3, 4].map((i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    );
  }

  const user = data?.user;
  const outpatientPct = Math.round((data?.outpatient_ratio || 0.85) * 100);
  const inpatientPct = Math.round((data?.inpatient_ratio || 0.90) * 100);

  return (
    <div className="didayi-page space-y-5">
      {/* Page Header */}
      <motion.div {...fadeIn}>
        <BrandedPageHeader title="权益全景" description="集中查看个人医保权益、保障范围、账户变化与使用记录。" badge="权益管家" status={<ApiStatusIndicator />} />
      </motion.div>

      {/* User Info Card */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.1 }}>
        <Card className="overflow-hidden border-0 bg-gradient-to-r from-cyan-500 via-sky-500 to-[#0876a8] text-white shadow-[0_16px_38px_rgba(14,149,190,.22)]">
          <CardContent className="p-6">
            <div className="flex items-center gap-4">
              <Avatar className="h-14 w-14 border-2 border-white/30">
                <AvatarFallback className="bg-white/20 text-white text-lg font-bold">
                  {user?.name?.charAt(0) || "张"}
                </AvatarFallback>
              </Avatar>
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-1">
                  <h2 className="text-xl font-bold">{user?.name || "张明"}</h2>
                  <Badge className="bg-white/20 text-white border-0 hover:bg-white/30">
                    {user?.insurance_type || "职工医保"}
                  </Badge>
                </div>
                <div className="flex items-center gap-4 text-sm text-white/80">
                  <span className="flex items-center gap-1">
                    <Calendar className="h-3.5 w-3.5" />
                    {user?.age || 45}岁
                  </span>
                  <span className="flex items-center gap-1">
                    <Building2 className="h-3.5 w-3.5" />
                    {user?.city || "演示城市"}
                  </span>
                  <span className="flex items-center gap-1">
                    <Shield className="h-3.5 w-3.5" />
                    参保状态：正常
                  </span>
                </div>
              </div>
              <div className="text-right">
                <p className="text-sm text-white/70">下次缴费</p>
                <p className="text-lg font-bold">{nextPaymentDate()}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Metric Cards */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.15 }}>
          <Card className="didayi-card">
            <CardContent className="p-6">
              <div className="flex items-center justify-between mb-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50">
                  <Clock className="h-5 w-5 text-blue-500" />
                </div>
                <Badge variant="secondary" className="text-xs bg-blue-50 text-blue-600">
                  累计
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground mb-1">缴费年限</p>
              <p className="text-2xl font-bold text-foreground">{data?.payment_years || "15年"}<span className="text-base font-normal text-muted-foreground">3个月</span></p>
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                  <span>退休需缴满25年</span>
                  <span>61%</span>
                </div>
                <Progress value={61} className="h-1.5" />
              </div>
            </CardContent>
          </Card>
        </motion.div>

        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.2 }}>
          <Card className="didayi-card">
            <CardContent className="p-6">
              <div className="flex items-center justify-between mb-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-green-50">
                  <Wallet className="h-5 w-5 text-green-500" />
                </div>
                <div className="flex items-center gap-1 text-xs text-green-600">
                  <ArrowUpRight className="h-3 w-3" />
                  +2.3%
                </div>
              </div>
              <p className="text-sm text-muted-foreground mb-1">账户余额</p>
              <p className="text-2xl font-bold text-foreground">¥{(data?.account_balance || 8562).toLocaleString()}<span className="text-base font-normal text-muted-foreground">.30</span></p>
              <div className="mt-3 flex items-center gap-1 text-xs text-muted-foreground">
                <TrendingUp className="h-3 w-3 text-green-500" />
                较上月增加 ¥340.00
              </div>
            </CardContent>
          </Card>
        </motion.div>

        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.25 }}>
          <Card className="didayi-card">
            <CardContent className="p-6">
              <div className="flex items-center justify-between mb-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-orange-50">
                  <Activity className="h-5 w-5 text-orange-500" />
                </div>
              </div>
              <p className="text-sm text-muted-foreground mb-1">门诊报销比例</p>
              <div className="flex items-center justify-between">
                <p className="text-2xl font-bold text-foreground">{outpatientPct}%</p>
                <CircularProgress value={outpatientPct} size={56} strokeWidth={4} color="#f97316" />
              </div>
            </CardContent>
          </Card>
        </motion.div>

        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.3 }}>
          <Card className="didayi-card">
            <CardContent className="p-6">
              <div className="flex items-center justify-between mb-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-purple-50">
                  <Shield className="h-5 w-5 text-purple-500" />
                </div>
              </div>
              <p className="text-sm text-muted-foreground mb-1">住院报销比例</p>
              <div className="flex items-center justify-between">
                <p className="text-2xl font-bold text-foreground">{inpatientPct}%</p>
                <CircularProgress value={inpatientPct} size={56} strokeWidth={4} color="#8b5cf6" />
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>

      {/* Charts and Activity */}
      <div className="grid gap-5 lg:grid-cols-3">
        {/* Payment History Chart */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.35 }} className="lg:col-span-2">
          <Card className="didayi-card">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-semibold">缴费记录</CardTitle>
                <Badge variant="secondary" className="text-xs">近12个月</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <ReactEChartsCore
                echarts={echarts}
                option={barChartOption}
                style={{ height: 280 }}
                notMerge
              />
            </CardContent>
          </Card>
        </motion.div>

        {/* Recent Activity Timeline */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.4 }}>
          <Card className="didayi-card h-full">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold">最近动态</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {recentActivities.map((activity, i) => (
                  <div key={i} className="flex items-start gap-3">
                    <div className="flex flex-col items-center">
                      <div
                        className={`flex h-7 w-7 items-center justify-center rounded-full ${
                          activity.type === "缴费"
                            ? "bg-blue-50 text-blue-500"
                            : "bg-green-50 text-green-500"
                        }`}
                      >
                        {activity.type === "缴费" ? (
                          <ArrowUpRight className="h-3.5 w-3.5" />
                        ) : (
                          <ArrowDownRight className="h-3.5 w-3.5" />
                        )}
                      </div>
                      {i < recentActivities.length - 1 && (
                        <div className="w-px h-4 bg-border mt-1" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <p className="text-sm font-medium truncate">{activity.desc}</p>
                        <span
                          className={`text-xs font-medium ${
                            activity.type === "缴费" ? "text-blue-600" : "text-green-600"
                          }`}
                        >
                          {activity.amount}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground">{activity.date}</p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </div>
  );
}
