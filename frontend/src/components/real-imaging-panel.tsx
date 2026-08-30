"use client";

/**
 * 真实公开影像数据集面板（科技医学风格）
 *
 * 展示 scripts/ingest_real_imaging.py 接入的真实影像数据集：
 * - 研究缩略卡片网格（AI 检测数 + GT 数 + IoU 精度指标）
 * - 点击阅片：AI 检测框（青色荧光）+ GT 标注（金色虚线）叠加 + 指标 + 政策联动
 *
 * 视觉：深色阅片工作站美学（暗底阅片、青/金双色框、网格纹理）
 */
import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Database,
  Loader2,
  X,
  Layers,
  ScanSearch,
  Target,
  ShieldCheck,
  FlaskConical,
  CircleDot,
  Eye,
} from "lucide-react";
import {
  getRealImagingStudies,
  getRealImagingDetail,
  type RealImagingListResponse,
  type RealImagingDetail,
} from "@/lib/api";

const SOURCE_META: Record<string, { label: string; real: boolean }> = {
  montgomery: { label: "Montgomery CXR", real: true },
  shenzhen: { label: "Shenzhen CXR", real: true },
  local: { label: "本地导入", real: true },
  demo: { label: "合成验证", real: false },
};

function isRealSource(source: string): boolean {
  return SOURCE_META[source]?.real ?? true;
}

export function RealImagingPanel() {
  const [list, setList] = useState<RealImagingListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<RealImagingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showGt, setShowGt] = useState(true);

  useEffect(() => {
    let mounted = true;
    getRealImagingStudies(undefined, undefined, 30).then((data) => {
      if (!mounted) return;
      setList(data);
      setLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, []);

  const openStudy = useCallback(async (studyId: string) => {
    setDetailLoading(true);
    setSelected(null);
    // with_vision=true：后端调用视觉大模型（GLM-4.6V）生成自然语言影像解读；
    // 未配置 Key 时后端自动降级（vision_interpretation=null），不影响弹层打开
    const d = await getRealImagingDetail(studyId, true);
    setSelected(d);
    setDetailLoading(false);
  }, []);

  if (loading) {
    return (
      <div className="flex h-32 items-center justify-center rounded-xl border border-sky-500/20 bg-slate-900/80">
        <Loader2 className="h-5 w-5 animate-spin text-sky-400" />
        <span className="ml-2 text-sm text-slate-400">加载真实影像数据集…</span>
      </div>
    );
  }

  if (!list || list.studies.length === 0) return null;

  const realCount = list.studies.filter((s) => isRealSource(s.source)).length;

  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45 }}
      className="relative overflow-hidden rounded-xl border border-sky-500/25 bg-slate-900 text-slate-100 shadow-[0_0_40px_-12px_rgba(56,189,248,0.35)]"
    >
      {/* 网格纹理背景 */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.3]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(56,189,248,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(56,189,248,0.05) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }}
      />
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-sky-400/70 to-transparent" />

      {/* 标题栏 */}
      <div className="relative flex flex-wrap items-center justify-between gap-3 border-b border-slate-700/60 px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-sky-500/15 ring-1 ring-sky-400/40">
            <Database className="h-4 w-4 text-sky-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold tracking-wide">真实公开数据集 · 影像科研验证</h3>
              {realCount > 0 && (
                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-400/40 bg-emerald-400/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                  <CircleDot className="h-3 w-3 animate-pulse" />
                  REAL DATA
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              公开胸片数据集（脱敏科研用途）· AI 病灶检测 → IoU 评估 → 医保政策联动
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px] text-slate-400">
          <span className="rounded border border-slate-600/60 bg-slate-800/60 px-2 py-1">
            {list.total} 项研究
          </span>
          {Object.entries(list.datasets).map(([k, v]) => (
            <span
              key={k}
              className={`rounded border px-2 py-1 ${
                isRealSource(k)
                  ? "border-sky-500/40 bg-sky-500/10 text-sky-300"
                  : "border-slate-600/60 bg-slate-800/60"
              }`}
            >
              {SOURCE_META[k]?.label || k} × {v.count}
            </span>
          ))}
        </div>
      </div>

      {/* 研究卡片网格 */}
      <div className="relative grid gap-3 p-5 sm:grid-cols-2 xl:grid-cols-3">
        {list.studies.map((s, i) => (
          <motion.button
            key={s.study_id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.05 * i }}
            onClick={() => openStudy(s.study_id)}
            className="group relative overflow-hidden rounded-lg border border-slate-700/60 bg-slate-800/50 p-4 text-left backdrop-blur-sm transition-all hover:border-sky-500/40 hover:bg-slate-800/80 hover:shadow-[0_0_20px_-6px_rgba(56,189,248,0.5)]"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-sm font-semibold text-sky-300">{s.study_id}</span>
              {isRealSource(s.source) && (
                <span className="rounded bg-emerald-400/15 px-1.5 py-0.5 text-[9px] font-bold tracking-wider text-emerald-300">
                  REAL
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              {s.study_label} · {SOURCE_META[s.source]?.label || s.source}
            </p>

            {/* 指标行 */}
            <div className="mt-3 grid grid-cols-3 gap-2 border-t border-slate-700/50 pt-3 text-center">
              <div>
                <p className="font-mono text-lg font-semibold text-cyan-300">{s.detected_count}</p>
                <p className="text-[10px] text-slate-500">AI 检出</p>
              </div>
              <div>
                <p className="font-mono text-lg font-semibold text-amber-300">{s.gt_count}</p>
                <p className="text-[10px] text-slate-500">GT 标注</p>
              </div>
              <div>
                <p className="font-mono text-lg font-semibold text-emerald-300">
                  {s.metrics?.mean_iou !== undefined
                    ? Number(s.metrics.mean_iou).toFixed(2)
                    : s.metrics?.precision !== undefined
                      ? Number(s.metrics.precision).toFixed(2)
                      : "—"}
                </p>
                <p className="text-[10px] text-slate-500">
                  {s.metrics?.mean_iou !== undefined ? "IoU" : "精度"}
                </p>
              </div>
            </div>

            <div className="mt-2 flex items-center justify-end text-[10px] text-slate-500 group-hover:text-sky-300">
              <ScanSearch className="mr-1 h-3.5 w-3.5" />
              阅片查看
            </div>
          </motion.button>
        ))}
      </div>

      {/* 底部免责声明 */}
      <div className="relative border-t border-slate-700/60 px-5 py-2.5">
        <p className="flex items-center gap-1.5 text-[10px] text-slate-500">
          <FlaskConical className="h-3 w-3" />
          {list.note || "真实公开数据集影像（脱敏科研用途），AI 检测仅供辅助参考。"}
        </p>
      </div>

      {/* 阅片弹层 */}
      <AnimatePresence>
        {(detailLoading || selected) && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
            onClick={() => {
              if (!detailLoading) {
                setSelected(null);
              }
            }}
          >
            <motion.div
              initial={{ scale: 0.96, y: 12 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.96, y: 12 }}
              transition={{ duration: 0.25 }}
              className="relative max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-xl border border-sky-500/30 bg-slate-900 shadow-[0_0_60px_-12px_rgba(56,189,248,0.6)]"
              onClick={(e) => e.stopPropagation()}
            >
              {/* 弹层头部 */}
              <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-700/60 bg-slate-900/95 px-5 py-3 backdrop-blur">
                <div className="flex items-center gap-3">
                  <Layers className="h-4 w-4 text-sky-400" />
                  <div>
                    <p className="font-mono text-sm font-semibold text-sky-300">
                      {selected?.study_id || "加载中…"}
                    </p>
                    <p className="text-xs text-slate-400">
                      {selected
                        ? `${selected.study_label} · ${SOURCE_META[selected.source]?.label || selected.source} · ${selected.origin_file}`
                        : "正在获取影像"}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {selected?.gt_findings && selected.gt_findings.length > 0 && (
                    <button
                      onClick={() => setShowGt((v) => !v)}
                      className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                        showGt
                          ? "border-amber-400/50 bg-amber-400/10 text-amber-300"
                          : "border-slate-600 text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      GT 标注 {showGt ? "开" : "关"}
                    </button>
                  )}
                  <button
                    onClick={() => setSelected(null)}
                    className="rounded-md border border-slate-600 p-1.5 text-slate-400 transition-colors hover:border-red-400/50 hover:text-red-300"
                    aria-label="关闭"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {detailLoading && (
                <div className="flex items-center justify-center gap-2 py-20">
                  <Loader2 className="h-5 w-5 animate-spin text-sky-400" />
                  <span className="text-sm text-slate-400">调取影像与检测数据…</span>
                </div>
              )}

              {selected && (
                <div className="grid gap-5 p-5 lg:grid-cols-5">
                  {/* 影像阅片区（暗底） */}
                  <div className="lg:col-span-3">
                    <div className="relative overflow-hidden rounded-lg border border-slate-700/60 bg-black">
                      {selected.image_base64 ? (
                        <img
                          src={selected.image_base64}
                          alt={selected.study_label}
                          className="block w-full"
                        />
                      ) : (
                        <div className="flex aspect-[4/3] items-center justify-center text-sm text-slate-500">
                          影像不可用
                        </div>
                      )}

                      {/* AI 检测框（青色荧光） */}
                      {(selected.detected_findings || []).map((f, i) => (
                        <div
                          key={`det-${i}`}
                          className="absolute border-2"
                          style={{
                            left: `${(f.x - f.w / 2) * 100}%`,
                            top: `${(f.y - f.h / 2) * 100}%`,
                            width: `${f.w * 100}%`,
                            height: `${f.h * 100}%`,
                            borderColor: "#22d3ee",
                            boxShadow: "0 0 10px rgba(34,211,238,0.5)",
                          }}
                        >
                          <span
                            className="absolute -top-5 left-0 whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold text-slate-900"
                            style={{ backgroundColor: "#22d3ee" }}
                          >
                            AI {f.finding_type} {Math.round(f.confidence * 100)}%
                          </span>
                        </div>
                      ))}

                      {/* GT 标注框（金色虚线） */}
                      {showGt &&
                        (selected.gt_findings || []).map((g, i) => (
                          <div
                            key={`gt-${i}`}
                            className="absolute border-2 border-dashed"
                            style={{
                              left: `${(g.x - g.w / 2) * 100}%`,
                              top: `${(g.y - g.h / 2) * 100}%`,
                              width: `${g.w * 100}%`,
                              height: `${g.h * 100}%`,
                              borderColor: "#fbbf24",
                            }}
                          >
                            <span
                              className="absolute -top-5 left-0 whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold text-slate-900"
                              style={{ backgroundColor: "#fbbf24" }}
                            >
                              GT {g.finding_type}
                            </span>
                          </div>
                        ))}

                      {/* 角标 */}
                      <div className="pointer-events-none absolute bottom-2 left-2 flex gap-1.5 font-mono text-[10px]">
                        <span className="rounded bg-cyan-400/20 px-2 py-0.5 text-cyan-200 backdrop-blur">
                          AI 检出 {selected.detected_findings?.length || 0}
                        </span>
                        <span className="rounded bg-amber-400/20 px-2 py-0.5 text-amber-200 backdrop-blur">
                          GT {selected.gt_findings?.length || 0}
                        </span>
                      </div>
                    </div>

                    {/* 图例 */}
                    <div className="mt-2 flex items-center gap-4 text-[11px] text-slate-400">
                      <span className="flex items-center gap-1.5">
                        <span className="h-0.5 w-4 bg-cyan-400" /> AI 检测框
                      </span>
                      <span className="flex items-center gap-1.5">
                        <span className="h-0.5 w-4 border-t-2 border-dashed border-amber-400" /> GT
                        医师标注
                      </span>
                    </div>
                  </div>

                  {/* 右侧：视觉解读 + 指标 + 政策联动 */}
                  <div className="space-y-4 lg:col-span-2">
                    {selected.vision_interpretation && (
                      <div className="rounded-lg border border-violet-500/30 bg-violet-500/5 p-4">
                        <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-violet-300">
                          <Eye className="h-4 w-4" /> 视觉大模型影像解读（GLM-4.6V）
                        </p>
                        <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-300">
                          {selected.vision_interpretation}
                        </p>
                      </div>
                    )}
                    <div className="rounded-lg border border-slate-700/60 bg-slate-900/70 p-4">
                      <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-sky-300">
                        <Target className="h-4 w-4" /> 检测评估指标
                      </p>
                      <div className="grid grid-cols-2 gap-2">
                        {Object.entries(selected.metrics || {}).map(([k, v]) => (
                          <div
                            key={k}
                            className="rounded border border-slate-700/50 bg-slate-800/60 px-3 py-2"
                          >
                            <p className="font-mono text-lg font-semibold text-cyan-300">
                              {typeof v === "number" ? v.toFixed(3) : String(v)}
                            </p>
                            <p className="text-[10px] uppercase tracking-wider text-slate-500">{k}</p>
                          </div>
                        ))}
                        {!selected.metrics || Object.keys(selected.metrics).length === 0 ? (
                          <p className="col-span-2 py-2 text-center text-xs text-slate-500">
                            无 GT 标注，仅展示 AI 检测结果
                          </p>
                        ) : null}
                      </div>
                    </div>

                    <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-4">
                      <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-indigo-300">
                        <ShieldCheck className="h-4 w-4" /> 医保政策联动
                      </p>
                      <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
                        {(selected.policy_links || []).map((l, i) => (
                          <div
                            key={i}
                            className="rounded border border-indigo-500/20 bg-slate-900/70 px-3 py-2"
                          >
                            <p className="text-xs font-medium text-slate-200">{l.title}</p>
                            <p className="mt-0.5 text-[11px] text-slate-400">{l.description}</p>
                          </div>
                        ))}
                        {!selected.policy_links || selected.policy_links.length === 0 ? (
                          <p className="py-3 text-center text-xs text-slate-500">
                            该研究无政策联动触发
                          </p>
                        ) : null}
                      </div>
                    </div>

                    <p className="rounded border border-slate-700/50 bg-slate-950/60 p-3 text-[10px] leading-relaxed text-slate-500">
                      {selected.disclaimer}
                    </p>
                  </div>
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}
