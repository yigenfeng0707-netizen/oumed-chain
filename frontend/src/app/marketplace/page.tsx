"use client";

/**
 * 数据要素市场（瓯医数链 · 流通闭环）
 *
 * 产品目录 → 交易申请（用途限定）→ 授权成交 → 收益分成（医院70/平台20/贡献者10）
 * → 审计存证链 → 监管方统计看板。
 */

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  ShoppingBag,
  Loader2,
  Link2,
  Scale,
  Building2,
  BadgeCheck,
  Gavel,
} from "lucide-react";
import {
  BarChart,
  PieChart,
} from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BrandedPageHeader } from "@/components/branded-page-header";
import {
  listDataProducts,
  purchaseDataProduct,
  listDataTransactions,
  getRegulatoryView,
  type DataProductItem,
  type DataTransactionItem,
  type RegulatoryView,
} from "@/lib/api";
import { cn } from "@/lib/utils";

echarts.use([
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

const TYPE_COLORS: Record<string, string> = {
  数据集: "#0891b2",
  模型API: "#7c3aed",
  治理产物: "#059669",
  算法服务: "#f59e0b",
};

export default function MarketplacePage() {
  const [products, setProducts] = useState<DataProductItem[]>([]);
  const [transactions, setTransactions] = useState<DataTransactionItem[]>([]);
  const [regulatory, setRegulatory] = useState<RegulatoryView | null>(null);
  const [buying, setBuying] = useState<string | null>(null);
  const [lastTx, setLastTx] = useState<DataTransactionItem | null>(null);

  const refresh = useCallback(async () => {
    const [p, t, r] = await Promise.all([
      listDataProducts(),
      listDataTransactions(),
      getRegulatoryView(),
    ]);
    if (p) setProducts(p);
    if (t) setTransactions(t);
    if (r) setRegulatory(r);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function buy(product: DataProductItem) {
    setBuying(product.id);
    try {
      const buyer = product.data_type === "模型API" || product.data_type === "算法服务"
        ? "健康卫士Agent"
        : "区域医共体数据中心";
      const tx = await purchaseDataProduct({
        product_id: product.id,
        buyer,
        purpose: buyer.includes("Agent") ? "智能体应用服务（患者风险预警）" : "区域临床科研分析（用途限定）",
      });
      if (tx) setLastTx(tx);
      await refresh();
    } finally {
      setBuying(null);
    }
  }

  const revenueOption = regulatory && {
    tooltip: { trigger: "item" as const },
    legend: { bottom: 0, itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    series: [{
      type: "pie" as const,
      radius: ["42%", "68%"],
      label: { show: true, formatter: "{b}\n¥{c}", fontSize: 11 },
      data: [
        { name: "医院（数据提供方70%）", value: regulatory.revenue.provider, itemStyle: { color: "#0891b2" } },
        { name: "平台（瓯医数链20%）", value: regulatory.revenue.platform, itemStyle: { color: "#7c3aed" } },
        { name: "数据贡献者10%", value: regulatory.revenue.contributor, itemStyle: { color: "#059669" } },
      ],
    }],
  };

  const typeOption = regulatory && {
    tooltip: { trigger: "item" as const },
    series: [{
      type: "pie" as const,
      radius: ["42%", "68%"],
      label: { show: true, formatter: "{b}: {c}", fontSize: 11 },
      data: Object.entries(regulatory.products_by_type).map(([name, value]) => ({
        name, value, itemStyle: { color: TYPE_COLORS[name] || "#94a3b8" },
      })),
    }],
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6">
      <BrandedPageHeader
        title="数据要素市场"
        description="产品目录 → 用途限定授权 → 收益分成（医院70% / 平台20% / 数据贡献者10%）→ 审计存证 → 监管合规"
        badge="流通闭环"
      />

      {/* 产品目录 */}
      <section className="space-y-3">
        <h2 className="flex items-center gap-2 text-base font-bold text-slate-800">
          <ShoppingBag className="h-4 w-4 text-cyan-600" />
          数据产品目录
          <span className="text-xs font-normal text-slate-400">共 {products.length} 件在售</span>
        </h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {products.map((p, i) => (
            <motion.div
              key={p.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
              className="flex flex-col rounded-2xl border border-sky-100 bg-white/90 p-4 shadow-sm"
            >
              <div className="mb-1 flex items-center justify-between">
                <span
                  className="rounded-full px-2 py-0.5 text-[10px] font-medium text-white"
                  style={{ backgroundColor: TYPE_COLORS[p.data_type] || "#94a3b8" }}
                >
                  {p.data_type}
                </span>
                <span className="flex items-center gap-1 text-[10px] text-slate-400">
                  <Building2 className="h-3 w-3" />
                  {p.provider}
                </span>
              </div>
              <div className="text-sm font-bold text-slate-800">{p.name}</div>
              <p className="mb-2 mt-1 line-clamp-2 flex-1 text-xs leading-5 text-slate-500">
                {p.description}
              </p>
              <div className="mb-2 flex flex-wrap gap-1">
                {p.sample_count > 0 && (
                  <span className="rounded-md bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-600">
                    {p.sample_count.toLocaleString()} 例
                  </span>
                )}
                <span className="rounded-md bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-600">
                  {p.privacy_tech}
                </span>
              </div>
              <div className="flex items-center justify-between border-t border-sky-50 pt-2">
                <div>
                  <span className="text-lg font-bold text-cyan-700">¥{p.price.toLocaleString()}</span>
                  <span className="text-xs text-slate-400"> /{p.price_unit}</span>
                </div>
                <button
                  onClick={() => buy(p)}
                  disabled={buying === p.id}
                  className={cn(
                    "flex items-center gap-1 rounded-lg bg-gradient-to-r from-cyan-500 to-sky-600 px-3 py-1.5 text-xs font-semibold text-white shadow transition-all",
                    buying === p.id && "cursor-not-allowed opacity-60"
                  )}
                >
                  {buying === p.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <BadgeCheck className="h-3.5 w-3.5" />}
                  申请授权
                </button>
              </div>
            </motion.div>
          ))}
          {!products.length && (
            <div className="col-span-full rounded-2xl border border-dashed border-sky-200 p-8 text-center text-sm text-slate-400">
              正在连接数据要素市场…（请确认后端已启动）
            </div>
          )}
        </div>
      </section>

      {/* 最近成交 + 存证链 */}
      {lastTx && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-2xl border border-emerald-200 bg-emerald-50/50 p-4"
        >
          <div className="flex items-center gap-2 text-sm font-bold text-emerald-700">
            <BadgeCheck className="h-4 w-4" />
            授权成交：{lastTx.product_name} → {lastTx.buyer}
          </div>
          <div className="mt-1 text-xs text-slate-600">
            金额 ¥{lastTx.amount.toLocaleString()} · 分成 医院¥{lastTx.revenue.provider} /
            平台¥{lastTx.revenue.platform} / 贡献者¥{lastTx.revenue.contributor} · 用途：{lastTx.purpose}
          </div>
          <div className="mt-1 font-mono text-[10px] text-slate-400">
            存证 {lastTx.prev_hash?.slice(0, 12)}… → {lastTx.event_hash?.slice(0, 12)}…
          </div>
        </motion.div>
      )}
      {transactions.length > 0 && (
        <section className="rounded-2xl border border-sky-100 bg-white/90 p-5 shadow-sm">
          <h2 className="mb-3 flex items-center gap-2 text-base font-bold text-slate-800">
            <Link2 className="h-4 w-4 text-cyan-600" />
            交易记录（审计存证链）
          </h2>
          <div className="space-y-2">
            {transactions.map((t) => (
              <div
                key={t.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-sky-50 bg-sky-50/40 px-3 py-2 text-xs"
              >
                <span className="font-semibold text-slate-700">{t.product_name}</span>
                <span className="text-slate-400">→</span>
                <span className="text-slate-600">{t.buyer}</span>
                <span className="font-bold text-cyan-700">¥{t.amount.toLocaleString()}</span>
                <span className="rounded-md bg-emerald-50 px-1.5 py-0.5 text-emerald-600">{t.status}</span>
                <span className="ml-auto font-mono text-[10px] text-slate-400">
                  {t.event_hash?.slice(0, 14)}…
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 监管看板 */}
      {regulatory && (
        <section className="rounded-2xl border border-sky-100 bg-white/90 p-5 shadow-sm">
          <h2 className="mb-3 flex items-center gap-2 text-base font-bold text-slate-800">
            <Gavel className="h-4 w-4 text-cyan-600" />
            监管方看板（卫健/数据局视角）
          </h2>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              { label: "累计交易", value: `${regulatory.total_transactions} 笔` },
              { label: "流通金额", value: `¥${regulatory.total_amount.toLocaleString()}` },
              { label: "在售产品", value: `${regulatory.product_count} 件` },
              { label: "隐私事件", value: `${regulatory.compliance.privacy_incidents} 起` },
            ].map((s) => (
              <div key={s.label} className="rounded-xl border border-sky-100 bg-sky-50/40 p-3 text-center">
                <div className="text-xs text-slate-500">{s.label}</div>
                <div className="text-lg font-bold text-slate-800">{s.value}</div>
              </div>
            ))}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {revenueOption && (
              <ReactEChartsCore echarts={echarts} option={revenueOption} style={{ height: 260 }} notMerge />
            )}
            {typeOption && (
              <ReactEChartsCore echarts={echarts} option={typeOption} style={{ height: 260 }} notMerge />
            )}
          </div>
          <div className="mt-2 flex items-center gap-1.5 rounded-xl bg-emerald-50/60 p-2.5 text-xs text-emerald-700">
            <Scale className="h-3.5 w-3.5 shrink-0" />
            {regulatory.compliance.note}
          </div>
        </section>
      )}
    </div>
  );
}
