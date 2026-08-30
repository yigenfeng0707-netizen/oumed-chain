"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Brain,
  Activity,
  Zap,
  Moon,
  Eye,
  HeartPulse,
  Heart,
  AlertTriangle,
  Play,
  Square,
  Loader2,
  Sparkles,
  ShieldCheck,
  TrendingUp,
  ChevronRight,
  Usb,
  Upload,
  CheckCircle2,
  AlertCircle,
  Wifi,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, LineChart, RadarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import {
  createEEGSession,
  createEEGSessionFromDevice,
  checkEEGDevice,
  importEEGFile,
  getLatestEEG,
  getEEGHistory,
  getEEGRealtime,
  getEEGMentalStates,
  type EEGDeviceStatus,
} from "@/lib/api";
import { useUser } from "@/lib/user-context";
import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { BrandedPageHeader } from "@/components/branded-page-header";
import { RealEEGPanel } from "@/components/real-eeg-panel";
import type {
  EEGSession,
  EEGMentalState,
  EEGRealtimeChunk,
  EEGTrendPoint,
} from "@/lib/mock-data";

echarts.use([
  BarChart,
  LineChart,
  RadarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  CanvasRenderer,
]);

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

// 通道颜色（Muse 4 通道布局）
const CHANNEL_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444"];
const CHANNEL_LABELS: Record<string, string> = {
  TP9: "左耳后",
  AF7: "左前额",
  AF8: "右前额",
  TP10: "右耳后",
};

// 频段中文标签 + 颜色
const BAND_META: Record<string, { label: string; color: string; desc: string }> = {
  delta: { label: "δ 波", color: "#6366f1", desc: "深度睡眠" },
  theta: { label: "θ 波", color: "#0ea5e9", desc: "疲劳/记忆" },
  alpha: { label: "α 波", color: "#10b981", desc: "放松/清醒" },
  beta: { label: "β 波", color: "#f59e0b", desc: "专注/焦虑" },
  gamma: { label: "γ 波", color: "#ef4444", desc: "高度认知" },
};

function getScoreColor(score: number, reverse = false): string {
  // reverse=true：分数越低越危险（如睡眠质量）
  const s = reverse ? 100 - score : score;
  if (s >= 70) return "#22c55e";
  if (s >= 40) return "#f59e0b";
  return "#ef4444";
}

function getScoreLabel(score: number, reverse = false): string {
  const s = reverse ? 100 - score : score;
  if (s >= 70) return "良好";
  if (s >= 40) return "一般";
  return "需关注";
}

function MetricRing({
  score,
  label,
  icon: Icon,
  reverse = false,
  unit = "/100",
}: {
  score: number;
  label: string;
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  reverse?: boolean;
  unit?: string;
}) {
  const size = 120;
  const strokeWidth = 8;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const color = getScoreColor(score, reverse);

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#f3f4f6" strokeWidth={strokeWidth} />
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
          <Icon className="h-5 w-5 mb-1" style={{ color }} />
          <span className="text-2xl font-bold" style={{ color }}>
            {Math.round(score)}
          </span>
        </div>
      </div>
      <div className="mt-2 text-center">
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="text-xs" style={{ color }}>
          {getScoreLabel(score, reverse)}
          {unit}
        </p>
      </div>
    </div>
  );
}

export default function EEGPage() {
  const [session, setSession] = useState<EEGSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [collecting, setCollecting] = useState(false);
  const [realtimeChunks, setRealtimeChunks] = useState<EEGRealtimeChunk[]>([]);
  const [trend, setTrend] = useState<EEGTrendPoint[]>([]);
  const [mentalStates, setMentalStates] = useState<EEGMentalState[]>([]);
  const [selectedState, setSelectedState] = useState<string>("auto");
  const [isStreaming, setIsStreaming] = useState(false);
  const streamTimerRef = useRef<NodeJS.Timeout | null>(null);
  const streamSeedRef = useRef(0);
  const { userId, currentUser } = useUser();

  // 采集模式：synthetic（合成信号）/ device（真实 LSL 设备）/ file（文件导入）
  const [acquireMode, setAcquireMode] = useState<"synthetic" | "device" | "file">("synthetic");
  // 真实设备状态
  const [deviceStatus, setDeviceStatus] = useState<EEGDeviceStatus | null>(null);
  const [checkingDevice, setCheckingDevice] = useState(false);
  // 文件导入
  const [importedFile, setImportedFile] = useState<File | null>(null);
  const [importSampleRate, setImportSampleRate] = useState<number>(256);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 错误提示
  const [acquireError, setAcquireError] = useState<string>("");

  // 加载初始数据
  useEffect(() => {
    setLoading(true);
    Promise.all([getLatestEEG(userId), getEEGHistory(userId), getEEGMentalStates()]).then(
      ([latest, history, states]) => {
        if (latest) setSession(latest);
        if (history?.trend) setTrend(history.trend);
        if (states?.length) setMentalStates(states);
        setLoading(false);
      },
    );
  }, [userId]);

  // 实时流采集
  const startStream = useCallback(() => {
    if (isStreaming) return;
    setIsStreaming(true);
    streamSeedRef.current = 0;
    setRealtimeChunks([]);

    const tick = async () => {
      const state = selectedState === "auto" ? "relaxed" : selectedState;
      const chunk = await getEEGRealtime(userId, state, streamSeedRef.current);
      if (chunk) {
        setRealtimeChunks((prev) => [...prev.slice(-30), chunk]); // 保留最近 30 个块
      }
      streamSeedRef.current += 1;
    };

    tick();
    streamTimerRef.current = setInterval(tick, 1000);
  }, [isStreaming, selectedState, userId]);

  const stopStream = useCallback(() => {
    setIsStreaming(false);
    if (streamTimerRef.current) {
      clearInterval(streamTimerRef.current);
      streamTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => stopStream();
  }, [stopStream]);

  // 发起完整采集会话
  const handleCollect = async () => {
    setCollecting(true);
    setAcquireError("");
    stopStream();
    try {
      let result: EEGSession | null = null;
      if (acquireMode === "device") {
        result = await createEEGSessionFromDevice(userId, 4, selectedState);
        if (!result) {
          setAcquireError(
            "真实设备采集失败。请确认：(1) LSL 流已启动（如 muselsl stream）；(2) 后端已安装 pylsl（pip install pylsl）；(3) 设备已连接并通过 /api/eeg/device/check 检测。",
          );
        }
      } else if (acquireMode === "file") {
        if (!importedFile) {
          setAcquireError("请先选择要导入的 EEG 文件（CSV/EDF/TXT）。");
          setCollecting(false);
          return;
        }
        result = await importEEGFile(userId, importedFile, importSampleRate, selectedState);
        if (!result) {
          setAcquireError(
            "文件导入失败。请确认文件格式：CSV 第一行为通道名、后续每行为采样点；EDF 需安装 pyedflib。可参考 docs/EEG设备接入指南.md。",
          );
        }
      } else {
        result = await createEEGSession(userId, selectedState, 4);
      }
      if (result) {
        setSession(result);
        // 刷新历史趋势
        const history = await getEEGHistory(userId);
        if (history?.trend) setTrend(history.trend);
      }
    } catch (err) {
      setAcquireError(`采集异常：${err instanceof Error ? err.message : String(err)}`);
    }
    setCollecting(false);
  };

  // 检查 LSL 设备连接状态
  const handleCheckDevice = async () => {
    setCheckingDevice(true);
    setAcquireError("");
    const status = await checkEEGDevice();
    setDeviceStatus(status);
    if (!status) {
      setAcquireError("无法连接后端 /api/eeg/device/check。请确认后端服务已启动。");
    } else if (!status.pylsl_installed) {
      setAcquireError("后端未安装 pylsl。请在后端环境执行：pip install pylsl");
    } else if (!status.connected) {
      setAcquireError(
        "未检测到 LSL EEG 流。请先启动设备的 LSL 输出（如 Muse: muselsl stream；OpenBCI: GUI 启用 LSL；Emotiv: Cortex App LSL Bridge）。",
      );
    }
    setCheckingDevice(false);
  };

  // 文件选择
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setImportedFile(f);
      setAcquireError("");
    }
  };

  // 波形图配置（4 通道）
  const waveformOption = {
    tooltip: { trigger: "axis" as const },
    legend: {
      data: (session?.channels || ["TP9", "AF7", "AF8", "TP10"]).map((c) => CHANNEL_LABELS[c] || c),
      top: 0,
      textStyle: { fontSize: 11 },
    },
    grid: { top: 40, right: 20, bottom: 30, left: 50 },
    xAxis: {
      type: "value" as const,
      name: "采样点",
      nameTextStyle: { fontSize: 10 },
      axisLabel: { fontSize: 10 },
    },
    yAxis: {
      type: "value" as const,
      name: "μV",
      nameTextStyle: { fontSize: 10 },
      axisLabel: { fontSize: 10 },
      splitLine: { lineStyle: { color: "#f3f4f6" } },
    },
    series: (session?.waveform || []).map((ch, i) => ({
      name: CHANNEL_LABELS[ch.channel] || ch.channel,
      type: "line",
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.2, color: CHANNEL_COLORS[i % 4] },
      itemStyle: { color: CHANNEL_COLORS[i % 4] },
      data: ch.data.map((p) => [p.i, Number(p.v.toFixed(2))]),
    })),
  };

  // 频段功率柱状图
  const bandPowerOption = {
    tooltip: { trigger: "axis" as const },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { top: 40, right: 20, bottom: 30, left: 50 },
    xAxis: {
      type: "category" as const,
      data: Object.keys(session?.avg_band_powers || {}).map((b) => BAND_META[b]?.label || b),
      axisLabel: { fontSize: 11 },
    },
    yAxis: { type: "value" as const, name: "功率", axisLabel: { fontSize: 10 } },
    series: [
      {
        name: "平均功率",
        type: "bar",
        data: Object.entries(session?.avg_band_powers || {}).map(([b, v]) => ({
          value: Number(v.toFixed(3)),
          itemStyle: { color: BAND_META[b]?.color || "#888" },
        })),
        barWidth: "50%",
        label: { show: true, position: "top" as const, fontSize: 10 },
      },
    ],
  };

  // 趋势图（4 维时序）
  const trendOption = {
    tooltip: { trigger: "axis" as const },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { top: 40, right: 20, bottom: 30, left: 40 },
    xAxis: {
      type: "category" as const,
      data: trend.map((_, i) => `第${i + 1}次`),
      axisLabel: { fontSize: 10 },
    },
    yAxis: { type: "value" as const, min: 0, max: 100, axisLabel: { fontSize: 10 } },
    series: [
      {
        name: "压力指数",
        type: "line",
        data: trend.map((t) => t.stress_index),
        smooth: true,
        lineStyle: { color: "#ef4444", width: 2 },
        itemStyle: { color: "#ef4444" },
      },
      {
        name: "注意力",
        type: "line",
        data: trend.map((t) => t.attention_index),
        smooth: true,
        lineStyle: { color: "#3b82f6", width: 2 },
        itemStyle: { color: "#3b82f6" },
      },
      {
        name: "睡眠质量",
        type: "line",
        data: trend.map((t) => t.sleep_quality),
        smooth: true,
        lineStyle: { color: "#6366f1", width: 2 },
        itemStyle: { color: "#6366f1" },
      },
      {
        name: "认知负荷",
        type: "line",
        data: trend.map((t) => t.cognitive_load),
        smooth: true,
        lineStyle: { color: "#f59e0b", width: 2 },
        itemStyle: { color: "#f59e0b" },
      },
    ],
  };

  if (loading) {
    return (
      <div className="didayi-page space-y-5">
        <motion.div {...fadeIn}>
          <h1 className="text-2xl font-bold text-foreground">脑电健康</h1>
          <p className="text-sm text-muted-foreground">关键医疗信号识别 · 脑电采集 → 频域分析 → 健康预警</p>
        </motion.div>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      </div>
    );
  }

  const metrics = session?.metrics;
  const alerts = session?.alerts || [];
  const policyLinks = session?.policy_links || [];

  return (
    <div className="didayi-page space-y-5">
      {/* 页头（科技医学风格） */}
      <motion.div {...fadeIn}>
        <BrandedPageHeader
          title="脑电健康"
          description={<>脑电采集 → 频域分析 → 健康预警 · 当前用户：<span className="font-semibold text-slate-700">{currentUser?.name || "用户"}</span></>}
          badge="BCI × 医保创新"
          status={<ApiStatusIndicator />}
        />
      </motion.div>

      {/* 采集控制栏 */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.05 }}>
        <Card className="didayi-card bg-gradient-to-r from-cyan-50/80 via-white to-sky-50/80">
          <CardContent className="p-4 space-y-3">
            {/* 第一行：采集模式切换 */}
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-foreground flex items-center gap-1">
                <Sparkles className="h-4 w-4 text-purple-500" />
                采集模式：
              </span>
              {([
                { key: "synthetic", label: "合成信号", icon: Sparkles, desc: "演示/测试用" },
                { key: "device", label: "真实设备", icon: Usb, desc: "LSL 实时采集" },
                { key: "file", label: "文件导入", icon: Upload, desc: "CSV/EDF/TXT" },
              ] as const).map((m) => {
                const active = acquireMode === m.key;
                const MIcon = m.icon;
                return (
                  <button
                    key={m.key}
                    onClick={() => {
                      setAcquireMode(m.key);
                      setAcquireError("");
                    }}
                    className={`px-3 py-1.5 text-sm rounded-lg border transition-all flex items-center gap-1.5 ${
                      active
                        ? "bg-purple-600 text-white border-purple-600 shadow-sm"
                        : "bg-white text-foreground border-gray-200 hover:border-purple-300"
                    }`}
                  >
                    <MIcon className="h-3.5 w-3.5" />
                    {m.label}
                    <span className={`text-[10px] ${active ? "text-purple-100" : "text-muted-foreground"}`}>
                      {m.desc}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* 第二行：场景选择 + 主操作按钮 */}
            <div className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">采集场景：</span>
                <select
                  value={selectedState}
                  onChange={(e) => setSelectedState(e.target.value)}
                  className="px-3 py-1.5 text-sm rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-purple-400"
                >
                  <option value="auto">智能推荐（根据画像）</option>
                  {mentalStates.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}（压力{s.stress}/注意力{s.attention}）
                    </option>
                  ))}
                </select>
              </div>

              <Button
                onClick={handleCollect}
                disabled={collecting || (acquireMode === "file" && !importedFile)}
                className="bg-purple-600 hover:bg-purple-700 text-white"
              >
                {collecting ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    采集中...
                  </>
                ) : acquireMode === "device" ? (
                  <>
                    <Usb className="h-4 w-4 mr-2" />
                    从设备采集 4 秒
                  </>
                ) : acquireMode === "file" ? (
                  <>
                    <Upload className="h-4 w-4 mr-2" />
                    导入并分析
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4 mr-2" />
                    发起 4 秒采集
                  </>
                )}
              </Button>

              {acquireMode === "synthetic" && (
                <Button
                  onClick={isStreaming ? stopStream : startStream}
                  variant={isStreaming ? "destructive" : "outline"}
                >
                  {isStreaming ? (
                    <>
                      <Square className="h-4 w-4 mr-2" />
                      停止实时流
                    </>
                  ) : (
                    <>
                      <Activity className="h-4 w-4 mr-2" />
                      实时流模拟
                    </>
                  )}
                </Button>
              )}

              {session && (
                <Badge variant="secondary" className="ml-auto">
                  最近采集：{session.mental_state_label} · {session.duration_seconds}s ·{" "}
                  {session.sample_rate}Hz · {session.channels.length} 通道
                </Badge>
              )}
            </div>

            {/* 第三行：模式专属控件 */}
            {acquireMode === "device" && (
              <div className="pt-2 border-t border-purple-100 flex flex-wrap items-center gap-3">
                <Button
                  onClick={handleCheckDevice}
                  variant="outline"
                  size="sm"
                  disabled={checkingDevice}
                >
                  {checkingDevice ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  ) : (
                    <Wifi className="h-3.5 w-3.5 mr-1.5" />
                  )}
                  检测 LSL 设备
                </Button>
                {deviceStatus && (
                  <div className="flex items-center gap-2 text-xs">
                    {deviceStatus.connected ? (
                      <CheckCircle2 className="h-4 w-4 text-green-500" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-amber-500" />
                    )}
                    <span className={deviceStatus.connected ? "text-green-700" : "text-amber-700"}>
                      {deviceStatus.message}
                    </span>
                    {deviceStatus.stream_count > 0 && (
                      <Badge variant="outline" className="text-[10px]">
                        {deviceStatus.stream_count} 个流
                      </Badge>
                    )}
                  </div>
                )}
                {deviceStatus?.streams && deviceStatus.streams.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {deviceStatus.streams.map((s, i) => (
                      <span
                        key={i}
                        className="text-[10px] px-2 py-0.5 rounded-full bg-white border border-purple-200 text-purple-700"
                      >
                        {s.name} · {s.channel_count}ch · {s.nominal_srate}Hz
                      </span>
                    ))}
                  </div>
                )}
                <span className="text-[11px] text-muted-foreground ml-auto">
                  支持 Muse / Emotiv / OpenBCI 等 LSL 兼容设备，详见 docs/EEG设备接入指南.md
                </span>
              </div>
            )}

            {acquireMode === "file" && (
              <div className="pt-2 border-t border-purple-100 flex flex-wrap items-center gap-3">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv,.edf,.txt,.tsv"
                  onChange={handleFileChange}
                  className="hidden"
                />
                <Button
                  onClick={() => fileInputRef.current?.click()}
                  variant="outline"
                  size="sm"
                >
                  <Upload className="h-3.5 w-3.5 mr-1.5" />
                  选择文件
                </Button>
                {importedFile && (
                  <Badge variant="secondary" className="text-xs">
                    {importedFile.name} ({(importedFile.size / 1024).toFixed(1)} KB)
                  </Badge>
                )}
                <div className="flex items-center gap-1.5 text-xs">
                  <span className="text-muted-foreground">采样率：</span>
                  <select
                    value={importSampleRate}
                    onChange={(e) => setImportSampleRate(Number(e.target.value))}
                    className="px-2 py-1 text-xs rounded border border-gray-200 bg-white"
                  >
                    <option value={256}>256 Hz</option>
                    <option value={250}>250 Hz</option>
                    <option value={500}>500 Hz</option>
                    <option value={1000}>1000 Hz</option>
                    <option value={220}>220 Hz（Muse）</option>
                  </select>
                </div>
                <span className="text-[11px] text-muted-foreground ml-auto">
                  CSV 格式：第一行通道名，后续每行采样点；EDF 需后端安装 pyedflib
                </span>
              </div>
            )}

            {/* 错误提示 */}
            {acquireError && (
              <div className="mt-2 p-2.5 rounded-lg bg-amber-50 border border-amber-200 flex items-start gap-2">
                <AlertCircle className="h-4 w-4 text-amber-600 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-amber-800 leading-relaxed">{acquireError}</p>
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {/* 实时流波形（采集时显示） */}
      <AnimatePresence>
        {isStreaming && realtimeChunks.length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
          >
            <Card className="bg-white border-purple-100">
              <CardHeader className="pb-2">
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <Activity className="h-4 w-4 text-purple-500 animate-pulse" />
                  实时脑电波形（{realtimeChunks.length} 个数据块）
                </CardTitle>
              </CardHeader>
              <CardContent>
                <RealtimeWaveform chunks={realtimeChunks} />
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 四维健康指标 */}
      {metrics && (
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.1 }}>
          <Card className="bg-white">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Brain className="h-4 w-4 text-purple-500" />
                脑电健康指标
                <Badge variant="outline" className="ml-2">
                  情绪：{metrics.emotion?.label || "平稳"}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <MetricRing score={metrics.stress_index} label="压力指数" icon={Zap} />
                <MetricRing score={metrics.attention_index} label="注意力" icon={Eye} />
                <MetricRing score={metrics.sleep_quality} label="睡眠质量" icon={Moon} reverse />
                <MetricRing score={metrics.cognitive_load} label="认知负荷" icon={Brain} />
              </div>
              {/* ⭐ 赛道7核心：脑血管风险 + 认知衰退 + 精神状态 */}
              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
                <MetricRing score={metrics.cerebrovascular_risk ?? 0} label="脑血管风险" icon={Heart} reverse />
                <MetricRing score={metrics.cognitive_decline_risk ?? 0} label="认知衰退风险" icon={Brain} reverse />
                <MetricRing score={metrics.mental_health?.overall_risk ?? 0} label="精神状态风险" icon={AlertTriangle} reverse />
              </div>
              {/* 精神状态筛查详情 */}
              {metrics.mental_health && metrics.mental_health.screening_label !== "正常" && (
                <div className="mt-3 p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm">
                  <span className="font-medium">精神状态筛查：</span>
                  <span className={metrics.mental_health.screening_label === "焦虑倾向" ? "text-orange-600" : metrics.mental_health.screening_label === "抑郁倾向" ? "text-blue-600" : "text-amber-600"}>
                    {metrics.mental_health.screening_label}
                  </span>
                  <span className="ml-3 text-muted-foreground">
                    焦虑 {metrics.mental_health.anxiety_score}/100 · 抑郁 {metrics.mental_health.depression_score}/100
                  </span>
                </div>
              )}
              {metrics.ratios && (
                <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-4">
                  <div className="text-center p-2 rounded bg-gray-50">
                    α/β = {metrics.ratios.alpha_beta}
                  </div>
                  <div className="text-center p-2 rounded bg-gray-50">
                    θ/β = {metrics.ratios.theta_beta}
                  </div>
                  <div className="text-center p-2 rounded bg-gray-50">
                    慢波占比 = {(metrics.ratios.slow_wave_ratio * 100).toFixed(1)}%
                  </div>
                  <div className="text-center p-2 rounded bg-gray-50">
                    快波占比 = {(metrics.ratios.fast_wave_ratio * 100).toFixed(1)}%
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* 波形 + 频段功率 */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.15 }}>
          <Card className="bg-white h-full">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Activity className="h-4 w-4 text-blue-500" />
                4 通道脑电波形
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ReactEChartsCore
                echarts={echarts}
                option={waveformOption}
                style={{ height: 280 }}
                notMerge
              />
            </CardContent>
          </Card>
        </motion.div>

        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.2 }}>
          <Card className="bg-white h-full">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Zap className="h-4 w-4 text-amber-500" />
                五频段功率谱
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ReactEChartsCore
                echarts={echarts}
                option={bandPowerOption}
                style={{ height: 280 }}
                notMerge
              />
              <div className="mt-2 grid grid-cols-3 gap-1 text-xs sm:grid-cols-5">
                {Object.entries(BAND_META).map(([k, v]) => (
                  <div key={k} className="text-center p-1.5 rounded bg-gray-50">
                    <div className="font-medium" style={{ color: v.color }}>
                      {v.label}
                    </div>
                    <div className="text-muted-foreground text-[10px]">{v.desc}</div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>

      {/* 脑电预警 */}
      {alerts.length > 0 && (
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.25 }}>
          <Card className="bg-white">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <HeartPulse className="h-4 w-4 text-red-500" />
                脑电健康预警
                <Badge variant="secondary">{alerts.length} 项</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {alerts.map((alert, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.3, delay: 0.3 + i * 0.05 }}
                    className={`p-3 rounded-lg border-l-4 ${
                      alert.level === "high"
                        ? "border-l-red-500 bg-red-50/50"
                        : alert.level === "medium"
                          ? "border-l-yellow-500 bg-yellow-50/50"
                          : "border-l-green-500 bg-green-50/50"
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-lg">{alert.icon}</span>
                      <div className="flex-1 min-w-0">
                        <h4 className="text-sm font-semibold text-foreground">{alert.title}</h4>
                        <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                          {alert.description || alert.desc}
                        </p>
                        {alert.suggestion && (
                          <p className="text-xs text-purple-600 mt-1.5">💡 {alert.suggestion}</p>
                        )}
                      </div>
                    </div>
                  </motion.div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* 医保政策联动（核心创新） */}
      {policyLinks.length > 0 && (
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.3 }}>
          <Card className="bg-gradient-to-br from-purple-50 to-blue-50 border-purple-200">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-purple-600" />
                脑电异常 → 医保政策联动
                <Badge className="bg-purple-600 text-white">脑电信号识别</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {policyLinks.map((link, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3, delay: 0.35 + i * 0.05 }}
                    className="p-4 rounded-lg bg-white border border-purple-100 shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1">
                          <h4 className="text-sm font-semibold text-foreground">{link.title}</h4>
                          <Badge variant="outline" className="text-xs text-purple-600 border-purple-300">
                            {link.policy_hint}
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground leading-relaxed">
                          {link.description}
                        </p>
                        <p className="text-xs text-purple-700 mt-1.5">💡 {link.suggestion}</p>
                        {link.related_policies.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {link.related_policies.map((p, j) => (
                              <span
                                key={j}
                                className="text-[10px] px-2 py-0.5 rounded-full bg-purple-100 text-purple-700"
                              >
                                {p}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <ChevronRight className="h-4 w-4 text-purple-400 flex-shrink-0 mt-1" />
                    </div>
                  </motion.div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* 历史趋势 */}
      {trend.length > 0 && (
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.35 }}>
          <Card className="bg-white">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-blue-500" />
                脑电健康趋势
                <Badge variant="secondary">最近 {trend.length} 次采集</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ReactEChartsCore
                echarts={echarts}
                option={trendOption}
                style={{ height: 280 }}
                notMerge
              />
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* 真实公开数据集（PhysioNet eegmmidb 科研验证） */}
      <RealEEGPanel />

      {/* 摘要 */}
      {session?.summary && (
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.4 }}>
          <Card className="bg-gray-50/50">
            <CardContent className="p-4">
              <div className="flex items-start gap-2">
                <Brain className="h-4 w-4 text-purple-500 mt-0.5 flex-shrink-0" />
                <p className="text-sm text-muted-foreground leading-relaxed">{session.summary}</p>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}
    </div>
  );
}

// 实时波形组件（滚动显示最近数据块）
function RealtimeWaveform({ chunks }: { chunks: EEGRealtimeChunk[] }) {
  const option = {
    animation: false,
    tooltip: { trigger: "axis" as const },
    grid: { top: 10, right: 10, bottom: 20, left: 40 },
    xAxis: {
      type: "value" as const,
      axisLabel: { fontSize: 10 },
    },
    yAxis: {
      type: "value" as const,
      name: "μV",
      nameTextStyle: { fontSize: 10 },
      axisLabel: { fontSize: 10 },
      splitLine: { lineStyle: { color: "#f3f4f6" } },
    },
    series: [
      {
        type: "line",
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.2, color: "#8b5cf6" },
        itemStyle: { color: "#8b5cf6" },
        areaStyle: { color: "rgba(139, 92, 246, 0.1)" },
        data: chunks.flatMap((c, ci) =>
          c.waveform.map((p) => [ci * 64 + p.i, Number(p.v.toFixed(2))]),
        ),
      },
    ],
  };
  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      style={{ height: 200 }}
      notMerge
    />
  );
}
