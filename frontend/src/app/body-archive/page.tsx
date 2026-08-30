"use client";

import { useMemo } from "react";
import { Activity, Box, Database, FileText, HeartPulse, Orbit, Radio, ShieldCheck, Users } from "lucide-react";

import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { DidaYiLogo } from "@/components/didayi-logo";
import { API_BASE } from "@/lib/api";
import { useUser } from "@/lib/user-context";

const capabilities = [
  { icon: Orbit, label: "自由旋转", detail: "360° 查看解剖结构" },
  { icon: HeartPulse, label: "部位档案", detail: "病史按器官自动归档" },
  { icon: Radio, label: "实时同步", detail: "新增资料自动更新" },
];

export default function BodyArchivePage() {
  const { currentUser, userId } = useUser();
  const viewerUrl = useMemo(() => {
    const base = API_BASE || "";
    const params = new URLSearchParams({ patient: userId });
    return `${base}/digital-body/index.html?${params.toString()}`;
  }, [userId]);
  const archiveBase = `${API_BASE || ""}/digital-body`;

  return (
    <div className="didayi-page space-y-5">
      <section className="relative overflow-hidden rounded-3xl border border-sky-100 bg-[linear-gradient(120deg,#e9faff_0%,#f5fcff_52%,#fff7f3_100%)] px-6 py-5 shadow-[0_14px_36px_rgba(30,134,185,.09)] lg:px-8">
        <div className="absolute -right-12 -top-20 h-56 w-56 rounded-full bg-cyan-300/20 blur-3xl" />
        <div className="absolute bottom-0 right-64 h-24 w-24 rounded-full bg-orange-200/25 blur-2xl" />
        <div className="relative flex flex-col justify-between gap-5 lg:flex-row lg:items-center">
          <div className="flex items-start gap-4">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-sky-500 text-white shadow-lg shadow-cyan-500/20">
              <Box className="h-6 w-6" />
            </div>
            <div>
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-bold tracking-tight text-slate-800">数字人体档案</h1>
                <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-600">LIVE</span>
              </div>
              <p className="text-sm leading-6 text-slate-500">
                为 <span className="font-semibold text-slate-700">{currentUser.name}</span> 构建可交互的个人解剖档案，让每条健康记录回到对应身体部位。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4 rounded-2xl border border-white/80 bg-white/70 px-4 py-3 shadow-sm backdrop-blur-sm">
            <DidaYiLogo />
            <div className="hidden border-l border-sky-100 pl-4 sm:block">
              <ApiStatusIndicator />
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-3">
        {capabilities.map(({ icon: Icon, label, detail }) => (
          <div key={label} className="didayi-card flex items-center gap-3 p-4">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-cyan-50 text-cyan-600">
              <Icon className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-slate-700">{label}</p>
              <p className="mt-0.5 text-xs text-slate-400">{detail}</p>
            </div>
          </div>
        ))}
      </section>

      <section className="flex flex-wrap gap-3">
        <a href={`${archiveBase}/cohort.html`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-xl bg-sky-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-sky-700">
          <Users className="h-4 w-4" />10 人数据整合总览
        </a>
        <a href={`${archiveBase}/dossier.html?patient=${userId}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-xl border border-sky-200 bg-white px-4 py-2.5 text-sm font-semibold text-sky-700 shadow-sm transition hover:bg-sky-50">
          <FileText className="h-4 w-4" />{currentUser.name}全量档案与下载
        </a>
      </section>

      <section className="overflow-hidden rounded-3xl border border-sky-100 bg-[#f4fbff] shadow-[0_22px_55px_rgba(44,142,184,.14)]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-sky-100 bg-[linear-gradient(90deg,#e8f8ff,#f7fcff)] px-5 py-3.5 text-slate-700">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-100 text-cyan-600">
              <Activity className="h-4 w-4" />
            </span>
            <div>
              <p className="text-sm font-semibold">{currentUser.name} · 人体健康映射</p>
              <p className="mt-0.5 text-[11px] text-slate-400">拖动旋转 · 滚轮缩放 · 点击发光部位查看档案</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            <Database className="h-3.5 w-3.5" />
            档案已连接
            <span className="mx-1 h-3 w-px bg-sky-200" />
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" />
            仅本机访问
          </div>
        </div>
        <iframe
          key={userId}
          src={viewerUrl}
          title={`${currentUser.name}的数字人体档案`}
          className="h-[calc(100vh-18rem)] min-h-[620px] w-full border-0"
          allow="fullscreen"
        />
      </section>

      <p className="flex items-start gap-2 px-1 text-xs leading-5 text-slate-400">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cyan-500" />
        本页面仅整理展示已有健康资料，不自动检测或推断疾病，不构成临床诊断或治疗建议。
      </p>
    </div>
  );
}
