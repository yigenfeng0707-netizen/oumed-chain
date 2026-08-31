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
  QrCode,
  X,
  Smartphone,
  CheckCircle2,
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
  precreatePayment,
  getPaymentOrder,
  completeSandboxPayment,
  type DataProductItem,
  type DataTransactionItem,
  type RegulatoryView,
  type PaymentOrderView,
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
  // 支付宝在线支付：扫码购买弹框（沙箱模拟 / live 收银台）
  const [payOrder, setPayOrder] = useState<PaymentOrderView | null>(null);
  const [paying, setPaying] = useState<string | null>(null);
  const [payError, setPayError] = useState("");

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

  /** 支付宝在线支付下单：弹出支付框（沙箱二维码 / live 收银台） */
  async function startPay(product: DataProductItem) {
    setPaying(product.id);
    setPayError("");
    try {
      const order = await precreatePayment({ kind: "marketplace", ref_id: product.id });
      if (!order) {
        setPayError("支付下单失败（后端不可达或产品不存在）");
        return;
      }
      setPayOrder(order);
    } finally {
      setPaying(null);
    }
  }

  /** 支付完成（轮询到 paid 或沙箱模拟成功）：关闭弹框并刷新交易链 */
  async function onPaid() {
    setPayOrder(null);
    await refresh();
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
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => startPay(p)}
                    disabled={paying === p.id}
                    title="支付宝在线支付（扫码购买，自动 70/20/10 分账上链）"
                    className={cn(
                      "flex items-center gap-1 rounded-lg border border-cyan-200 bg-cyan-50 px-2.5 py-1.5 text-xs font-semibold text-cyan-700 transition-all hover:bg-cyan-100",
                      paying === p.id && "cursor-not-allowed opacity-60"
                    )}
                  >
                    {paying === p.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <QrCode className="h-3.5 w-3.5" />}
                    扫码购买
                  </button>
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
              </div>
            </motion.div>
          ))}
          {!products.length && (
            <div className="col-span-full rounded-2xl border border-dashed border-sky-200 p-8 text-center text-sm text-slate-400">
              正在连接数据要素市场…（请确认后端已启动）
            </div>
          )}
        </div>
        {payError && (
          <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">{payError}</div>
        )}
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

      {/* 支付宝在线支付：支付弹框 */}
      {payOrder && <PaymentDialog order={payOrder} onClose={() => setPayOrder(null)} onPaid={onPaid} />}
    </div>
  );
}

// ============================================================
// 扫码支付弹框（沙箱模拟 / live 真码）
// ============================================================

/** 确定性伪二维码渲染（零依赖）：对载荷哈希生成稳定点阵，仅作演示视觉 */
function PseudoQr({ payload, size = 168 }: { payload: string; size?: number }) {
  const n = 21;
  const cell = size / n;
  // 简易字符串哈希 → 确定性伪随机序列（mulberry32）
  let h = 2166136261;
  for (let i = 0; i < payload.length; i++) {
    h ^= payload.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  let s = h >>> 0;
  const rnd = () => {
    s |= 0; s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const inFinder = (r: number, c: number) =>
    (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7);
  const finderDark = (r: number, c: number) => {
    const lr = r < 7 ? r : r - (n - 7);
    const lc = c < 7 ? c : c - (n - 7);
    return lr === 0 || lr === 6 || lc === 0 || lc === 6 || (lr >= 2 && lr <= 4 && lc >= 2 && lc <= 4);
  };
  const cells: boolean[] = [];
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      cells.push(inFinder(r, c) ? finderDark(r, c) : rnd() < 0.46);
    }
  }
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="rounded-lg bg-white p-2 shadow-inner">
      {cells.map((dark, i) =>
        dark ? (
          <rect key={i} x={(i % n) * cell} y={Math.floor(i / n) * cell} width={cell + 0.3} height={cell + 0.3} fill="#1e293b" />
        ) : null
      )}
    </svg>
  );
}

function PaymentDialog({
  order,
  onClose,
  onPaid,
}: {
  order: PaymentOrderView;
  onClose: () => void;
  onPaid: () => void;
}) {
  const [phase, setPhase] = useState<"waiting" | "completing" | "paid">("waiting");
  const [proof, setProof] = useState<string | null>(null);
  const isSandbox = order.gateway === "sandbox";

  // live 模式：轮询订单状态等待支付宝异步回调（沙箱由「模拟扫码」驱动）
  useEffect(() => {
    if (isSandbox || phase !== "waiting") return;
    const timer = setInterval(async () => {
      const latest = await getPaymentOrder(order.order_no);
      if (latest?.status === "paid") {
        setProof(latest.pay_proof);
        setPhase("paid");
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [isSandbox, phase, order.order_no]);

  async function simulateScan() {
    setPhase("completing");
    const r = await completeSandboxPayment(order.order_no);
    if (r?.status === "paid") {
      setProof(r.pay_proof ?? null);
      setPhase("paid");
    } else {
      setPhase("waiting");
    }
  }

  /** live：新窗口写入支付宝收银台表单并自动提交（收银台支持扫码付） */
  function openCashier() {
    if (!order.pay_form) return;
    const w = window.open("", "_blank");
    if (!w) return;
    w.document.write(`${order.pay_form}<script>document.querySelector('form').submit();<\/script>`);
    w.document.close();
  }

  const yuan = (order.amount_cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2 });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/45 p-4" onClick={phase === "waiting" ? onClose : undefined}>
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-xl"
      >
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-bold text-slate-800">
            <QrCode className="h-4 w-4 text-cyan-600" />
            支付宝在线支付
            <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium", isSandbox ? "bg-amber-50 text-amber-600" : "bg-emerald-50 text-emerald-600")}>
              {isSandbox ? "沙箱演示" : "真实收款"}
            </span>
          </div>
          {phase === "waiting" && (
            <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="mb-1 text-xs text-slate-500">{order.subject}</div>
        <div className="mb-3 text-2xl font-bold text-slate-900">¥{yuan}</div>

        {phase !== "paid" ? (
          <>
            {isSandbox ? (
              <>
                <div className="flex justify-center">
                  <PseudoQr payload={order.qr_code || order.order_no} />
                </div>
                <p className="mt-2 text-center text-[11px] leading-4 text-slate-400">
                  演示模式：下方二维码为模拟载荷，点击下方按钮模拟扫码支付
                </p>
              </>
            ) : (
              <>
                <p className="mt-2 text-center text-[11px] leading-4 text-slate-400">
                  点击下方按钮打开支付宝收银台（支持支付宝扫码支付）；
                  支付完成后本页自动确认并分账上链，请勿重复下单。
                </p>
                <button
                  onClick={openCashier}
                  disabled={!order.pay_form}
                  className={cn(
                    "mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-cyan-500 to-sky-600 py-2 text-sm font-semibold text-white shadow",
                    !order.pay_form && "cursor-not-allowed opacity-60"
                  )}
                >
                  <Smartphone className="h-4 w-4" />
                  打开支付宝收银台
                </button>
              </>
            )}
            <div className="mt-1 text-center font-mono text-[10px] text-slate-300">订单号 {order.order_no}</div>
            {isSandbox && (
              <button
                onClick={simulateScan}
                disabled={phase === "completing"}
                className={cn(
                  "mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-cyan-500 to-sky-600 py-2 text-sm font-semibold text-white shadow",
                  phase === "completing" && "cursor-not-allowed opacity-60"
                )}
              >
                {phase === "completing" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Smartphone className="h-4 w-4" />}
                模拟扫码支付
              </button>
            )}
          </>
        ) : (
          <div className="py-4 text-center">
            <CheckCircle2 className="mx-auto mb-2 h-12 w-12 text-emerald-500" />
            <div className="text-base font-bold text-emerald-700">支付成功</div>
            <div className="mt-1 text-xs text-slate-500">已自动完成 70/20/10 分账并写入存证链</div>
            {proof && <div className="mt-2 font-mono text-[10px] text-slate-400">支付凭证 {proof}</div>}
            <button
              onClick={onPaid}
              className="mt-4 w-full rounded-lg bg-emerald-500 py-2 text-sm font-semibold text-white shadow hover:bg-emerald-600"
            >
              查看交易存证
            </button>
          </div>
        )}
      </motion.div>
    </div>
  );
}
