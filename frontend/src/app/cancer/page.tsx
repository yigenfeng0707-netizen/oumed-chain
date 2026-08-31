"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { motion } from "framer-motion";
import {
  Target,
  Play,
  Loader2,
  Users,
  Activity,
  Info,
  ImageOff,
  ScanEye,
  History,
} from "lucide-react";
import {
  getCancerStatus,
  predictCancer,
  getCancerCohort,
  predictCohortPatient,
  getCancerHistory,
} from "@/lib/api";
import { useUser } from "@/lib/user-context";
import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { BrandedPageHeader } from "@/components/branded-page-header";
import type {
  CancerReport,
  CancerStatus,
  CancerCohortPatient,
  CancerCohortDetail,
} from "@/lib/mock-data";

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

const LEVEL_BADGE: Record<string, string> = {
  "高": "bg-rose-100 text-rose-700",
  "中": "bg-amber-100 text-amber-700",
  "低": "bg-green-100 text-green-700",
  "队列基线": "bg-slate-100 text-slate-600",
};

const LEVEL_BAR: Record<string, string> = {
  "高": "bg-rose-500",
  "中": "bg-amber-500",
  "低": "bg-green-500",
  "队列基线": "bg-slate-400",
};

const MODE_LABEL: Record<string, string> = {
  fused: "EHR + 胸片融合",
  ehr_only: "仅健康档案 (EHR)",
  img_only: "仅胸片影像",
};

function RiskBar({ item, max = 1 }: { item: { cancer_zh: string; prob: number; level: string }; max?: number }) {
  const pct = Math.min(100, (item.prob / (max || 1)) * 100);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-slate-700">{item.cancer_zh}</span>
        <span className="flex items-center gap-2">
          <span className="font-mono text-slate-600">{(item.prob * 100).toFixed(1)}%</span>
          <Badge className={`${LEVEL_BADGE[item.level] ?? LEVEL_BADGE["低"]} border-0`}>{item.level}</Badge>
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className={`h-full rounded-full ${LEVEL_BAR[item.level] ?? LEVEL_BAR["低"]}`}
        />
      </div>
    </div>
  );
}

export default function CancerPage() {
  const { userId, currentUser } = useUser();
  const [status, setStatus] = useState<CancerStatus | null>(null);
  const [report, setReport] = useState<CancerReport | null>(null);
  const [predicting, setPredicting] = useState(false);
  const [patients, setPatients] = useState<CancerCohortPatient[]>([]);
  const [selectedPid, setSelectedPid] = useState<string | null>(null);
  const [detail, setDetail] = useState<CancerCohortDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [historyCount, setHistoryCount] = useState(0);

  const loadStatus = useCallback(async () => {
    setStatus(await getCancerStatus());
  }, []);

  const loadCohort = useCallback(async () => {
    const data = await getCancerCohort();
    if (data) setPatients(data.patients);
  }, []);

  useEffect(() => {
    loadStatus();
    loadCohort();
  }, [loadStatus, loadCohort]);

  useEffect(() => {
    if (userId) getCancerHistory(userId, 1).then((h) => setHistoryCount(h.length));
  }, [userId, report]);

  const runPredict = async () => {
    setPredicting(true);
    try {
      const r = await predictCancer(userId);
      setReport(r);
    } finally {
      setPredicting(false);
    }
  };

  const selectPatient = async (pid: string) => {
    setSelectedPid(pid);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await predictCohortPatient(pid));
    } finally {
      setDetailLoading(false);
    }
  };

  const realModel = status?.engine === "oncoformer";

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 md:p-6">
      <motion.div {...fadeIn}>
        <BrandedPageHeader
          title="泛癌卫士 · Oncoformer 泛癌风险评估"
          description={<>温附医团队 Cell 2026 泛癌模型 × 7 种常见癌种即时/未来风险（当前用户：<span className="font-semibold text-slate-700">{currentUser.name}</span>）</>}
          badge="真模型推理 × 多模态门控"
          status={<ApiStatusIndicator />}
        />
      </motion.div>

      {/* 服务形态徽标条 */}
      <motion.div {...fadeIn} className="flex flex-wrap items-center gap-3">
        <Badge className={realModel ? "border-0 bg-rose-100 text-rose-700" : "border-0 bg-slate-100 text-slate-600"}>
          <Activity className="mr-1 h-3.5 w-3.5" />
          {realModel ? "真模型实时推理" : "预计算队列模式"}
        </Badge>
        <span className="text-xs text-slate-500">{status?.model ?? "Oncoformer (demo ckpt)"}</span>
        <Badge variant="outline" className="border-slate-200 text-slate-500">
          <Users className="mr-1 h-3 w-3" /> COMPASS 示例队列 {status?.cohort_patients ?? patients.length} 例
        </Badge>
        {historyCount > 0 && (
          <Badge variant="outline" className="border-slate-200 text-slate-500">
            <History className="mr-1 h-3 w-3" /> 已存档 {historyCount} 次预测
          </Badge>
        )}
      </motion.div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* 当前用户预测 */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Target className="h-4 w-4 text-rose-600" /> 我的泛癌风险评估
            </CardTitle>
            <Button size="sm" onClick={runPredict} disabled={predicting} className="bg-rose-600 hover:bg-rose-700">
              {predicting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />}
              {predicting ? "推理中…" : "开始评估"}
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {!report && !predicting && (
              <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500">
                点击「开始评估」：平台将把您的健康档案合成为研究队列特征空间的就诊序列，
                由 Oncoformer 真模型输出 7 种癌种的即时与未来风险。
              </div>
            )}
            {predicting && !report && (
              <div className="flex h-32 items-center justify-center gap-2 text-sm text-slate-500">
                <Loader2 className="h-5 w-5 animate-spin" /> 模型推理中（首次调用需加载权重，约 30 秒）…
              </div>
            )}
            {report && (
              <>
                <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">{report.note}</div>
                {(["concurrent", "future"] as const).map((horizon) => {
                  const rows = report.risks[horizon];
                  if (!rows?.length) return null;
                  return (
                    <div key={horizon} className="space-y-2.5">
                      <div className="text-sm font-semibold text-slate-700">
                        {horizon === "concurrent" ? "即时诊断风险（当前就诊）" : "未来风险（诊断前预测窗口）"}
                      </div>
                      {rows.slice(0, 4).map((r) => (
                        <RiskBar key={`${horizon}-${r.cancer}`} item={r} />
                      ))}
                    </div>
                  );
                })}
                {report.pred_age != null && report.profile_age != null && (
                  <div className="rounded-lg border border-sky-100 bg-sky-50 px-3 py-2 text-xs text-sky-700">
                    模型从就诊序列推断年龄 {report.pred_age.toFixed(0)} 岁 · 档案年龄 {report.profile_age} 岁
                    （推断偏差是模型可信度的参考信号）
                  </div>
                )}
                <div className="flex items-start gap-1.5 text-xs text-slate-400">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {report.disclaimer}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* COMPASS 示例队列 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <ScanEye className="h-4 w-4 text-sky-600" /> COMPASS 示例队列 · 三模态对比
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="max-h-40 space-y-1.5 overflow-y-auto pr-1">
              {patients.map((p) => (
                <button
                  key={p.pid}
                  onClick={() => selectPatient(p.pid)}
                  className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                    selectedPid === p.pid
                      ? "border-sky-300 bg-sky-50 text-sky-800"
                      : "border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  <span className="font-mono text-xs">{p.pid}</span>
                  <span className="flex items-center gap-1.5">
                    {p.meta.cancers_present.length > 0 ? (
                      <Badge className="border-0 bg-rose-100 text-rose-700">
                        阳性 · {p.meta.cancer_stage}
                      </Badge>
                    ) : (
                      <Badge className="border-0 bg-green-100 text-green-700">对照</Badge>
                    )}
                    {!p.meta.has_image && <ImageOff className="h-3 w-3 text-slate-300" />}
                  </span>
                </button>
              ))}
              {patients.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-200 p-4 text-center text-xs text-slate-400">
                  队列数据加载中或未随部署提供
                </div>
              )}
            </div>

            {detailLoading && (
              <div className="flex h-24 items-center justify-center gap-2 text-sm text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin" /> 模型推理中…
              </div>
            )}
            {detail && !detailLoading && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <Badge variant="outline" className="border-slate-200">
                    {detail.engine === "oncoformer" ? "实时推理" : "预计算结果"}
                  </Badge>
                  <span>真实脱敏队列患者 · 就诊 {Object.values(detail.modes)[0]?.n_visits ?? "—"} 次</span>
                  {detail.meta.cancers_present.length > 0 && (
                    <span className="text-rose-600">确诊：{detail.meta.cancers_present.join("、")}</span>
                  )}
                </div>
                {(["concurrent", "future"] as const).map((horizon) => {
                  const fusedScores = detail.modes.fused?.scores?.[horizon] ?? {};
                  const topCancers = Object.entries(fusedScores)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 4)
                    .map(([c]) => c);
                  if (!topCancers.length) return null;
                  return (
                    <div key={horizon} className="space-y-2">
                      <div className="text-sm font-semibold text-slate-700">
                        {horizon === "concurrent" ? "即时诊断风险" : "未来风险"}（Top 4 · 三模态对比）
                      </div>
                      {topCancers.map((c) => {
                        const probs = ["fused", "ehr_only", "img_only"].map(
                          (m) => detail.modes[m]?.scores?.[horizon]?.[c] ?? 0,
                        );
                        const maxV = Math.max(...probs, 0.05);
                        return (
                          <div key={`${horizon}-${c}`} className="space-y-1">
                            <div className="text-xs font-medium text-slate-600">{c}</div>
                            {(["fused", "ehr_only", "img_only"] as const).map((m, i) => (
                              <div key={m} className="flex items-center gap-2">
                                <span className="w-24 shrink-0 text-[10px] text-slate-400">{MODE_LABEL[m]}</span>
                                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                                  <div
                                    className={["h-full rounded-full bg-sky-500", "h-full rounded-full bg-indigo-400", "h-full rounded-full bg-teal-400"][i]}
                                    style={{ width: `${(probs[i] / maxV) * 100}%` }}
                                  />
                                </div>
                                <span className="w-12 shrink-0 text-right font-mono text-[10px] text-slate-500">
                                  {(probs[i] * 100).toFixed(1)}%
                                </span>
                              </div>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
                <div className="flex items-start gap-1.5 text-xs text-slate-400">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {status?.disclaimer}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
