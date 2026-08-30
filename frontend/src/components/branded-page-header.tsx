import type { ReactNode } from "react";
import { Sparkles } from "lucide-react";

import { DidaYiLogo } from "@/components/didayi-logo";

type BrandedPageHeaderProps = {
  title: string;
  description: ReactNode;
  badge?: string;
  status?: ReactNode;
};

/** 全站统一的瓯医数链业务页头。 */
export function BrandedPageHeader({ title, description, badge = "智能服务", status }: BrandedPageHeaderProps) {
  return (
    <section className="relative overflow-hidden rounded-3xl border border-sky-100 bg-[linear-gradient(120deg,#e9faff_0%,#f6fdff_55%,#fff7f3_100%)] px-6 py-5 shadow-[0_14px_36px_rgba(30,134,185,.09)] lg:px-8">
      <div className="absolute -right-12 -top-20 h-56 w-56 rounded-full bg-cyan-300/20 blur-3xl" />
      <div className="absolute bottom-0 right-64 h-24 w-24 rounded-full bg-orange-200/25 blur-2xl" />
      <div className="relative flex flex-col justify-between gap-5 lg:flex-row lg:items-center">
        <div className="flex items-start gap-4">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-sky-500 text-white shadow-lg shadow-cyan-500/20">
            <Sparkles className="h-6 w-6" />
          </span>
          <div>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-bold tracking-tight text-slate-800">{title}</h1>
              <span className="rounded-full border border-cyan-200 bg-white/75 px-2.5 py-1 text-[11px] font-semibold text-cyan-700">{badge}</span>
            </div>
            <p className="text-sm leading-6 text-slate-500">{description}</p>
          </div>
        </div>
        <div className="flex items-center gap-4 rounded-2xl border border-white/80 bg-white/70 px-4 py-3 shadow-sm backdrop-blur-sm">
          <DidaYiLogo />
          {status ? <div className="hidden border-l border-sky-100 pl-4 sm:block">{status}</div> : null}
        </div>
      </div>
    </section>
  );
}
