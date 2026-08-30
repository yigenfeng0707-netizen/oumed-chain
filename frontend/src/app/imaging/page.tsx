"use client";

import { useState, useEffect, useCallback, useRef, type MouseEvent } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { motion, AnimatePresence } from "framer-motion";
import {
  ScanLine,
  Play,
  Loader2,
  Check,
  X,
  Plus,
  ShieldAlert,
  ShieldCheck,
  Stethoscope,
  History,
  Crosshair,
  AlertTriangle,
  Sparkles,
  FileText,
  Landmark,
  MousePointerClick,
  Eye,
} from "lucide-react";
import {
  analyzeImaging,
  reviewImaging,
  getImagingRecords,
  getImagingStudyTypes,
  type ImagingAnnotation,
} from "@/lib/api";
import { useUser } from "@/lib/user-context";
import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { BrandedPageHeader } from "@/components/branded-page-header";
import { RealImagingPanel } from "@/components/real-imaging-panel";
import type {
  ImagingStudyResponse,
  ImagingStudyTypeInfo,
  ImagingFindingItem,
  ImagingReportData,
  ImagingPolicyLink,
  ImagingRecordItem,
} from "@/lib/mock-data";

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

const SEVERITY_STYLE: Record<
  string,
  { label: string; badge: string; border: string }
> = {
  high: { label: "高危", badge: "bg-red-100 text-red-700", border: "border-red-500" },
  medium: { label: "中危", badge: "bg-amber-100 text-amber-700", border: "border-amber-500" },
  low: { label: "低危", badge: "bg-green-100 text-green-700", border: "border-green-500" },
};

const FINDING_COLORS = [
  "#ef4444",
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
  "#84cc16",
];

const RISK_BADGE: Record<string, string> = {
  高风险: "bg-red-100 text-red-700",
  中风险: "bg-amber-100 text-amber-700",
  待复核: "bg-blue-100 text-blue-700",
  低风险: "bg-green-100 text-green-700",
};

export default function ImagingPage() {
  const { userId, currentUser } = useUser();
  const [studyTypes, setStudyTypes] = useState<Record<string, ImagingStudyTypeInfo>>({});
  const [selectedType, setSelectedType] = useState("chest_xray");
  const [study, setStudy] = useState<ImagingStudyResponse | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reviewMap, setReviewMap] = useState<Record<number, "confirm" | "reject">>({});
  const [addedFindings, setAddedFindings] = useState<ImagingFindingItem[]>([]);
  const [finalReport, setFinalReport] = useState<ImagingReportData | null>(null);
  const [finalLinks, setFinalLinks] = useState<ImagingPolicyLink[]>([]);
  const [records, setRecords] = useState<ImagingRecordItem[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [notice, setNotice] = useState<string>("");
  const [loadingHistory, setLoadingHistory] = useState(false);
  // 视觉大模型（GLM-4.6V）影像解读开关：后端未配置 Key 时自动降级跳过
  const [withVision, setWithVision] = useState(true);

  // 新增标注：点击影像定位
  const imageBoxRef = useRef<HTMLDivElement>(null);
  const [pickMode, setPickMode] = useState(false);
  const [pickPos, setPickPos] = useState<{ x: number; y: number } | null>(null);
  const [addForm, setAddForm] = useState({
    finding_type: "nodule",
    w: 0.1,
    h: 0.1,
    severity: "medium" as "low" | "medium" | "high",
  });

  useEffect(() => {
    let mounted = true;
    getImagingStudyTypes().then((types) => {
      if (!mounted || !types) return;
      setStudyTypes(types);
      const first = Object.keys(types)[0];
      if (first) {
        setSelectedType(first);
        setAddForm((f) => ({
          ...f,
          finding_type: types[first].findings[0]?.key || "nodule",
        }));
      }
    });
    return () => {
      mounted = false;
    };
  }, []);

  const loadRecords = useCallback(async () => {
    setLoadingHistory(true);
    const list = await getImagingRecords(userId, 8);
    if (list) setRecords(list);
    setLoadingHistory(false);
  }, [userId]);

  const runAnalysis = async () => {
    setAnalyzing(true);
    setStudy(null);
    setReviewMap({});
    setAddedFindings([]);
    setFinalReport(null);
    setFinalLinks([]);
    setPickPos(null);
    setNotice("");
    const result = await analyzeImaging(userId, selectedType, undefined, undefined, withVision);
    setAnalyzing(false);
    if (!result) {
      setNotice("后端未响应，请确认 API 服务已启动。");
      return;
    }
    setStudy(result);
  };

  const handleImageClick = (e: MouseEvent<HTMLDivElement>) => {
    if (!pickMode || !imageBoxRef.current) return;
    const rect = imageBoxRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setPickPos({ x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) });
  };

  const addFinding = () => {
    if (!pickPos) return;
    const label =
      studyTypes[selectedType]?.findings.find((f) => f.key === addForm.finding_type)
        ?.label || addForm.finding_type;
    setAddedFindings((prev) => [
      ...prev,
      {
        finding_type: addForm.finding_type,
        label,
        x: pickPos.x,
        y: pickPos.y,
        w: addForm.w,
        h: addForm.h,
        confidence: 1,
        severity: addForm.severity,
        source: "doctor",
        status: "confirmed",
        evidence: "医师人工复核新增标注",
      },
    ]);
    setPickPos(null);
    setPickMode(false);
  };

  const submitReview = async () => {
    if (!study) return;
    const annotations: ImagingAnnotation[] = [];
    study.findings.forEach((f, i) => {
      const op = reviewMap[i];
      if (op === "confirm" || op === "reject") {
        annotations.push({ action: op, index: i, finding_type: f.finding_type, x: f.x, y: f.y, w: f.w, h: f.h, confidence: f.confidence, severity: f.severity, evidence: f.evidence });
      }
    });
    addedFindings.forEach((f) => {
      annotations.push({
        action: "add",
        finding_type: f.finding_type,
        x: f.x,
        y: f.y,
        w: f.w,
        h: f.h,
        confidence: f.confidence,
        severity: f.severity,
        evidence: f.evidence,
      });
    });

    setSubmitting(true);
    const recordId = study.record_id ?? records[0]?.id ?? 0;
    const result = await reviewImaging(userId, recordId, annotations);
    setSubmitting(false);
    if (!result) {
      setNotice("复核提交失败，请确认后端服务已启动。");
      return;
    }
    setFinalReport(result.report);
    setFinalLinks(result.policy_links);
    setNotice("复核完成，最终结构化报告已生成。");
  };

  // 影像上叠加的标注框（AI 发现 + 医生新增 + 定位点）
  const renderBoxes = () => {
    if (!study) return null;
    const boxes: Array<{ el: JSX.Element }> = [];
    const colorOf = (i: number) => FINDING_COLORS[i % FINDING_COLORS.length];
    study.findings.forEach((f, i) => {
      const op = reviewMap[i];
      if (op === "reject") return;
      const color = op === "confirm" ? "#22c55e" : colorOf(i);
      const dimmed = op === "confirm";
      boxes.push({
        el: (
          <div
            key={`ai-${i}`}
            className={`absolute border-2 ${SEVERITY_STYLE[f.severity]?.border || "border-sky-500"}`}
            style={{
              left: `${(f.x - f.w / 2) * 100}%`,
              top: `${(f.y - f.h / 2) * 100}%`,
              width: `${f.w * 100}%`,
              height: `${f.h * 100}%`,
              borderColor: color,
              opacity: dimmed ? 0.9 : 1,
              boxShadow: `0 0 0 1px rgba(0,0,0,0.3)`,
            }}
          >
            <span
              className="absolute -top-6 left-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-semibold text-white"
              style={{ backgroundColor: color }}
            >
              {f.label} {Math.round(f.confidence * 100)}% · {SEVERITY_STYLE[f.severity]?.label || f.severity}
            </span>
          </div>
        ),
      });
    });
    addedFindings.forEach((f, i) => {
      boxes.push({
        el: (
          <div
            key={`doc-${i}`}
            className="absolute border-2 border-dashed border-purple-500"
            style={{
              left: `${(f.x - f.w / 2) * 100}%`,
              top: `${(f.y - f.h / 2) * 100}%`,
              width: `${f.w * 100}%`,
              height: `${f.h * 100}%`,
            }}
          >
            <span className="absolute -top-6 left-0 whitespace-nowrap rounded bg-purple-500 px-1.5 py-0.5 text-[11px] font-semibold text-white">
              医生新增 · {f.label}
            </span>
          </div>
        ),
      });
    });
    return boxes.map((b) => b.el);
  };

  const counts = {
    total: study?.findings.length || 0,
    confirmed: Object.values(reviewMap).filter((v) => v === "confirm").length,
    rejected: Object.values(reviewMap).filter((v) => v === "reject").length,
    added: addedFindings.length,
  };

  return (
    <div className="didayi-page space-y-5">
      <motion.div {...fadeIn}>
        <BrandedPageHeader
          title="影像卫士 · AI 医学影像标注工作台"
          description={<>AI 病灶检测预标注 → 医师逐框复核 → 结构化报告与医保政策联动（当前患者：<span className="font-semibold text-slate-700">{currentUser.name}</span>）</>}
          badge="AI 预标注 × 医师复核"
          status={<ApiStatusIndicator />}
        />
      </motion.div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* 左列：检查类型 + 发现列表 + 新增标注 */}
        <div className="space-y-6 lg:col-span-1">
          <motion.div {...fadeIn}>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Stethoscope className="h-4 w-4 text-primary" /> 检查类型
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-3 gap-2">
                  {Object.entries(studyTypes).map(([key, info]) => (
                    <button
                      key={key}
                      onClick={() => {
                        setSelectedType(key);
                        setAddForm((f) => ({ ...f, finding_type: info.findings[0]?.key || "nodule" }));
                      }}
                      className={`rounded-lg border p-3 text-center transition-all ${
                        selectedType === key
                          ? "border-primary bg-primary/5 shadow-sm"
                          : "border-border hover:border-primary/50"
                      }`}
                    >
                      <span className={`block text-sm font-semibold ${selectedType === key ? "text-primary" : ""}`}>
                        {info.label}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {info.findings.length} 类病灶
                      </span>
                    </button>
                  ))}
                </div>
                <div className="rounded-lg bg-muted/50 p-3">
                  <p className="mb-2 text-xs font-medium text-muted-foreground">可检测病灶</p>
                  <div className="flex flex-wrap gap-1.5">
                    {studyTypes[selectedType]?.findings.map((f) => (
                      <Badge key={f.key} variant="secondary" className="text-xs">
                        {f.label}
                      </Badge>
                    ))}
                  </div>
                </div>
                <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-violet-200 bg-violet-50/60 px-3 py-2 text-sm text-violet-700 transition-colors hover:bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-300">
                  <input
                    type="checkbox"
                    checked={withVision}
                    onChange={(e) => setWithVision(e.target.checked)}
                    className="h-4 w-4 accent-violet-600"
                  />
                  <Eye className="h-4 w-4" />
                  视觉大模型解读
                </label>
                <Button onClick={runAnalysis} disabled={analyzing} className="w-full" size="lg">
                  {analyzing ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" /> AI 影像分析中…
                    </>
                  ) : (
                    <>
                      <Play className="mr-2 h-4 w-4" /> 开始 AI 分析
                    </>
                  )}
                </Button>
              </CardContent>
            </Card>
          </motion.div>

          <motion.div {...fadeIn}>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <ShieldAlert className="h-4 w-4 text-primary" /> AI 发现与复核
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {!study && (
                  <p className="py-6 text-center text-sm text-muted-foreground">
                    先选择检查类型并点击「开始 AI 分析」
                  </p>
                )}
                {study && study.findings.length === 0 && (
                  <p className="py-6 text-center text-sm text-muted-foreground">
                    未检测到异常发现
                  </p>
                )}
                {study?.findings.map((f, i) => {
                  const op = reviewMap[i];
                  return (
                    <div
                      key={i}
                      className={`rounded-lg border p-3 transition-all ${
                        op === "confirm"
                          ? "border-green-300 bg-green-50/60"
                          : op === "reject"
                            ? "border-red-200 bg-red-50/50 opacity-60"
                            : "border-border"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span
                            className="h-2.5 w-2.5 rounded-full"
                            style={{ backgroundColor: FINDING_COLORS[i % FINDING_COLORS.length] }}
                          />
                          <span className="text-sm font-semibold">{f.label}</span>
                          <Badge className={SEVERITY_STYLE[f.severity]?.badge}>
                            {SEVERITY_STYLE[f.severity]?.label || f.severity}
                          </Badge>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {Math.round(f.confidence * 100)}%
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{f.evidence || "AI 自动检测"}</p>
                      <div className="mt-2 flex gap-2">
                        {op !== "confirm" && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 border-green-400 text-green-700 hover:bg-green-50"
                            onClick={() =>
                              setReviewMap((m) => ({ ...m, [i]: "confirm" }))
                            }
                          >
                            <Check className="mr-1 h-3.5 w-3.5" /> 确认
                          </Button>
                        )}
                        {op !== "reject" && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 border-red-300 text-red-600 hover:bg-red-50"
                            onClick={() => setReviewMap((m) => ({ ...m, [i]: "reject" }))}
                          >
                            <X className="mr-1 h-3.5 w-3.5" /> 驳回
                          </Button>
                        )}
                        {op && (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-muted-foreground"
                            onClick={() =>
                              setReviewMap((m) => {
                                const next = { ...m };
                                delete next[i];
                                return next;
                              })
                            }
                          >
                            撤销
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
                {addedFindings.map((f, i) => (
                  <div key={`add-${i}`} className="rounded-lg border border-purple-300 bg-purple-50/50 p-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Plus className="h-3.5 w-3.5 text-purple-600" />
                        <span className="text-sm font-semibold">医生新增 · {f.label}</span>
                        <Badge className={SEVERITY_STYLE[f.severity]?.badge}>
                          {SEVERITY_STYLE[f.severity]?.label}
                        </Badge>
                      </div>
                      <button
                        className="text-xs text-muted-foreground hover:text-red-600"
                        onClick={() => setAddedFindings((prev) => prev.filter((_, j) => j !== i))}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </motion.div>
        </div>

        {/* 右列：影像 + 标注 + 报告 */}
        <div className="space-y-6 lg:col-span-2">
          <motion.div {...fadeIn}>
            <Card>
              <CardHeader className="flex flex-row items-center justify-between pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <ScanLine className="h-4 w-4 text-primary" />
                  {study ? `${study.study_label} · 影像 #${study.study_id}` : "影像浏览"}
                </CardTitle>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant={pickMode ? "default" : "outline"}
                    className="h-8"
                    disabled={!study}
                    onClick={() => {
                      setPickMode((m) => !m);
                      setPickPos(null);
                    }}
                  >
                    {pickMode ? (
                      <>
                        <MousePointerClick className="mr-1 h-3.5 w-3.5" /> 点击影像定位
                      </>
                    ) : (
                      <>
                        <Plus className="mr-1 h-3.5 w-3.5" /> 新增标注
                      </>
                    )}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8"
                    onClick={() => {
                      setShowHistory((s) => !s);
                      if (!showHistory && records.length === 0) loadRecords();
                    }}
                  >
                    <History className="mr-1 h-3.5 w-3.5" /> 历史
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div
                  ref={imageBoxRef}
                  onClick={handleImageClick}
                  className={`relative overflow-hidden rounded-xl border bg-black ${
                    pickMode ? "cursor-crosshair ring-2 ring-primary" : ""
                  }`}
                >
                  {study?.image_base64 ? (
                    <img src={study.image_base64} alt={study.study_label} className="block w-full" />
                  ) : study ? (
                    <div className="flex aspect-[4/3] w-full items-center justify-center text-sm text-muted-foreground">
                      影像渲染中…
                    </div>
                  ) : (
                    <div className="flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 text-muted-foreground">
                      <ScanLine className="h-10 w-10 opacity-40" />
                      <p className="text-sm">选择检查类型后开始 AI 分析，生成影像并自动标注</p>
                    </div>
                  )}
                  {study && renderBoxes()}
                  {pickPos && (
                    <div
                      className="pointer-events-none absolute h-4 w-4 -translate-x-1/2 -translate-y-1/2"
                      style={{ left: `${pickPos.x * 100}%`, top: `${pickPos.y * 100}%` }}
                    >
                      <Crosshair className="h-4 w-4 text-yellow-300 drop-shadow" />
                    </div>
                  )}
                  {study && (
                    <div className="pointer-events-none absolute bottom-2 left-2 flex flex-wrap gap-1.5">
                      <Badge className="bg-black/60 text-white">AI 预标注 {counts.total}</Badge>
                      <Badge className="bg-green-600/80 text-white">确认 {counts.confirmed}</Badge>
                      <Badge className="bg-red-500/80 text-white">驳回 {counts.rejected}</Badge>
                      <Badge className="bg-purple-600/80 text-white">医生新增 {counts.added}</Badge>
                    </div>
                  )}
                </div>

                <AnimatePresence>
                  {pickMode && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="overflow-hidden rounded-lg border border-primary/30 bg-primary/5 p-4"
                    >
                      <p className="mb-3 text-sm font-medium">
                        在影像上点击病灶中心位置，然后填写标注信息：
                      </p>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="mb-1 block text-xs text-muted-foreground">病灶类别</label>
                          <select
                            value={addForm.finding_type}
                            onChange={(e) => setAddForm((f) => ({ ...f, finding_type: e.target.value }))}
                            className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                          >
                            {studyTypes[selectedType]?.findings.map((f) => (
                              <option key={f.key} value={f.key}>
                                {f.label}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <label className="mb-1 block text-xs text-muted-foreground">严重度</label>
                          <select
                            value={addForm.severity}
                            onChange={(e) =>
                              setAddForm((f) => ({
                                ...f,
                                severity: e.target.value as "low" | "medium" | "high",
                              }))
                            }
                            className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                          >
                            <option value="high">高危</option>
                            <option value="medium">中危</option>
                            <option value="low">低危</option>
                          </select>
                        </div>
                        <div>
                          <label className="mb-1 block text-xs text-muted-foreground">宽度 {Math.round(addForm.w * 100)}%</label>
                          <input
                            type="range"
                            min={0.03}
                            max={0.3}
                            step={0.01}
                            value={addForm.w}
                            onChange={(e) => setAddForm((f) => ({ ...f, w: Number(e.target.value) }))}
                            className="w-full"
                          />
                        </div>
                        <div>
                          <label className="mb-1 block text-xs text-muted-foreground">高度 {Math.round(addForm.h * 100)}%</label>
                          <input
                            type="range"
                            min={0.03}
                            max={0.3}
                            step={0.01}
                            value={addForm.h}
                            onChange={(e) => setAddForm((f) => ({ ...f, h: Number(e.target.value) }))}
                            className="w-full"
                          />
                        </div>
                      </div>
                      <div className="mt-3 flex gap-2">
                        <Button size="sm" disabled={!pickPos} onClick={addFinding}>
                          <Plus className="mr-1 h-3.5 w-3.5" /> 添加标注
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setPickMode(false)}>
                          取消
                        </Button>
                        {!pickPos && (
                          <span className="self-center text-xs text-muted-foreground">请在影像上点击定位</span>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                <AnimatePresence>
                  {showHistory && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="rounded-lg border bg-muted/30 p-3"
                    >
                      <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                        <History className="h-4 w-4" /> 影像检查历史
                      </p>
                      {loadingHistory && (
                        <p className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2 className="h-3 w-3 animate-spin" /> 加载中…
                        </p>
                      )}
                      {records.length === 0 && !loadingHistory && (
                        <p className="text-xs text-muted-foreground">暂无历史记录</p>
                      )}
                      <div className="max-h-52 space-y-1.5 overflow-y-auto">
                        {records.map((r) => (
                          <div
                            key={r.id}
                            className="flex items-center justify-between rounded-md border bg-background px-3 py-2 text-sm"
                          >
                            <div>
                              <span className="font-medium">{r.study_label}</span>
                              <span className="ml-2 text-xs text-muted-foreground">{r.created_at}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-muted-foreground">
                                {r.finding_count} 处 · {r.policy_link_count} 条联动
                              </span>
                              <Badge className={RISK_BADGE[r.risk_level] || "bg-slate-100 text-slate-700"}>
                                {r.risk_level}
                              </Badge>
                            </div>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {notice && (
                  <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-700">
                    <Sparkles className="h-4 w-4" /> {notice}
                  </div>
                )}
              </CardContent>
            </Card>
          </motion.div>

          <motion.div {...fadeIn}>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <FileText className="h-4 w-4 text-primary" />
                  {finalReport ? "最终结构化报告" : "AI 报告与医保联动"}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {study?.vision_interpretation && (
                  <div className="rounded-lg border border-violet-200 bg-violet-50/60 p-3 dark:border-violet-500/30 dark:bg-violet-500/10">
                    <p className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold text-violet-700 dark:text-violet-300">
                      <Eye className="h-4 w-4" /> 视觉大模型影像解读（GLM-4.6V）
                    </p>
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/80">
                      {study.vision_interpretation}
                    </p>
                  </div>
                )}
                {study && !finalReport && (
                  <>
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm">{study.report.conclusion}</p>
                      <Badge className={RISK_BADGE[study.report.risk_level] || "bg-slate-100 text-slate-700"}>
                        {study.report.risk_level}
                      </Badge>
                    </div>
                    <ul className="space-y-1 text-sm text-muted-foreground">
                      {study.report.advice.map((a, i) => (
                        <li key={i} className="flex gap-1.5">
                          <span className="text-primary">·</span> {a}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {finalReport && (
                  <>
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm">{finalReport.conclusion}</p>
                      <Badge className={RISK_BADGE[finalReport.risk_level] || "bg-slate-100 text-slate-700"}>
                        {finalReport.risk_level}
                      </Badge>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge className="bg-green-100 text-green-700">确认 {finalReport.confirmed_count}</Badge>
                      <Badge className="bg-red-100 text-red-700">驳回 {finalReport.rejected_count}</Badge>
                      {finalReport.advice.map((a, i) => (
                        <Badge key={i} variant="outline">{a}</Badge>
                      ))}
                    </div>
                  </>
                )}

                <div>
                  <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                    <Landmark className="h-4 w-4 text-primary" /> 医保政策联动推荐
                  </p>
                  {(finalLinks.length > 0 || study?.policy_links.length) ? (
                    <div className="grid gap-2">
                      {(finalLinks.length > 0 ? finalLinks : study?.policy_links || []).map((link, i) => (
                        <div key={i} className="rounded-lg border border-primary/20 bg-primary/5 p-3">
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">{link.title}</span>
                            <Badge variant="secondary" className="text-xs">{link.trigger}</Badge>
                          </div>
                          <p className="mt-1 text-xs text-muted-foreground">{link.description}</p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {link.related_policies.map((p, j) => (
                              <Badge key={j} variant="outline" className="text-xs">{p}</Badge>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="rounded-lg bg-muted/40 p-3 text-center text-sm text-muted-foreground">
                      完成分析后展示影像异常与医保政策的联动推荐
                    </p>
                  )}
                </div>

                {study && (
                  <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
                    <p className="text-xs text-muted-foreground">
                      <AlertTriangle className="mr-1 inline h-3.5 w-3.5" />
                      {study.disclaimer}
                    </p>
                    <Button
                      onClick={submitReview}
                      disabled={submitting || counts.confirmed + counts.rejected + counts.added === 0}
                      size="lg"
                    >
                      {submitting ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 生成最终报告…
                        </>
                      ) : (
                        <>
                          <ShieldCheck className="mr-2 h-4 w-4" /> 提交医生复核
                        </>
                      )}
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          </motion.div>
        </div>
      </div>

      {/* 真实公开影像数据集（科研验证） */}
      <RealImagingPanel />
    </div>
  );
}
