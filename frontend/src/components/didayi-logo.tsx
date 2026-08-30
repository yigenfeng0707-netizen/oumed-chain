import { cn } from "@/lib/utils";

type DidaYiLogoProps = {
  className?: string;
  compact?: boolean;
  light?: boolean;
};

/** 瓯医数链品牌标志：数据要素可信流通。 */
export function DidaYiLogo({ className, compact = false, light = false }: DidaYiLogoProps) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <span
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 to-sky-600 text-white shadow-lg shadow-cyan-500/25"
        aria-hidden="true"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-6 w-6">
          <circle cx="18" cy="5" r="3" />
          <circle cx="6" cy="12" r="3" />
          <circle cx="18" cy="19" r="3" />
          <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
          <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
        </svg>
      </span>
      {!compact && (
        <div className="min-w-0">
          <div className={cn("text-xl font-bold leading-none tracking-[.04em]", light ? "text-slate-800" : "text-slate-900")}>瓯医数链</div>
          <div className={cn("mt-1 text-[10px] tracking-[.08em]", light ? "text-slate-500" : "text-slate-500")}>医疗数据 · 可用不可见</div>
        </div>
      )}
    </div>
  );
}
