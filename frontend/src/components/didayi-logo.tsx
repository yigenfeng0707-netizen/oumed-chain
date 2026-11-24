import { cn } from "@/lib/utils";

type DidaYiLogoProps = {
  className?: string;
  compact?: boolean;
  light?: boolean;
  /** size="sm"：窄容器（如侧栏 w-64，文字区约 164px）单行展示完整品牌名 */
  size?: "default" | "sm";
};

/** 浙江电信·医疗数链平台品牌标志：数据要素可信流通。 */
export function DidaYiLogo({ className, compact = false, light = false, size = "default" }: DidaYiLogoProps) {
  // 侧栏单行版：15px × 10 字 ≈ 150px < 可用宽度，tracking 归零避免溢出换行
  const nameCls = size === "sm"
    ? "text-[15px] font-bold leading-none tracking-normal whitespace-nowrap"
    : "text-xl font-bold leading-none tracking-[.04em]";
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
          <div className={cn(nameCls, light ? "text-slate-800" : "text-slate-900")}>浙江电信·医疗数链平台</div>
          <div className={cn("mt-1 text-[10px] tracking-[.08em]", light ? "text-slate-500" : "text-slate-500")}>医疗数据 · 可用不可见</div>
        </div>
      )}
    </div>
  );
}
