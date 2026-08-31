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
        className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-white shadow-lg shadow-cyan-500/25"
        aria-hidden="true"
      >
        <img src="/logo.jpg" alt="" className="h-full w-full object-cover" />
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
