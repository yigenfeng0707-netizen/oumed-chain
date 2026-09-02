# -*- coding: utf-8 -*-
"""瓯医数链 BP Word 专业图表生成（浅色印刷风，300dpi，2026-09-02 事实口径）

输出：docs/BP素材/charts/*.png（供 build_bp_docx.py 嵌入 Word）
运行：backend/.venv/Scripts/python scripts/bp_charts.py
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "BP素材", "charts")
os.makedirs(OUT, exist_ok=True)

# 浅色印刷风配色
INK = "#1E293B"        # 正文深灰蓝
SUB = "#64748B"        # 次级灰
GRID = "#E2E8F0"       # 网格
BLUE = "#0891B2"       # 主色 青
NAVY = "#1E5BFF"       # 辅色 蓝
ORANGE = "#F97316"     # 强调 橙
GOLD = "#D4A017"       # 强调 金
GREEN = "#059669"      # 成功 绿
LIGHT = "#F1F5F9"      # 底色


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("bottom", "left"):
        ax.spines[s].set_color("#CBD5E1")
    ax.tick_params(colors=SUB, labelsize=11)
    ax.yaxis.grid(True, color=GRID, linestyle="--", alpha=0.9)
    ax.set_axisbelow(True)


def _bar_label(ax, bar, v, fmt="{:.4f}"):
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.004,
            fmt.format(v), ha="center", va="bottom", color=INK,
            fontsize=11, fontweight="bold")


def chart_auc_dual():
    """图1 双基准 AUC 对比（左：UCI 真实患者；右：合成三院）"""
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2), dpi=300)
    fig.suptitle("联邦学习效果双基准验证：真实患者数据与合成数据均追平集中训练上界",
                 fontsize=15, fontweight="bold", color=INK, y=1.00)

    ax = axes[0]
    data = [("A机构·老年层", 0.8362), ("B机构·中年层", 0.8427),
            ("C机构·青年层", 0.9351), ("联邦学习\n(数据不出院)", 0.9091),
            ("集中训练上界\n(现实不可行)", 0.9019)]
    colors = ["#93B4E0", "#93B4E0", "#93B4E0", BLUE, ORANGE]
    bars = ax.bar([d[0] for d in data], [d[1] for d in data],
                  color=colors, width=0.6, zorder=3)
    for bar, (n, v) in zip(bars, data):
        _bar_label(ax, bar, v)
    ax.set_ylim(0.78, 0.96)
    _style(ax)
    ax.set_ylabel("全局测试 AUC", color=INK, fontsize=12)
    ax.set_title("UCI 心脏病真实队列 · 297 例真实患者\n按年龄三分位构造非 IID 三机构",
                 fontsize=12.5, color=INK, pad=10)

    ax = axes[1]
    data = [("三甲医院", 0.7013), ("县医院", 0.6994), ("社区中心", 0.6896),
            ("联邦学习\n(数据不出院)", 0.7018), ("集中训练上界", 0.7012)]
    colors = ["#93B4E0", "#93B4E0", "#93B4E0", BLUE, ORANGE]
    bars = ax.bar([d[0] for d in data], [d[1] for d in data],
                  color=colors, width=0.6, zorder=3)
    for bar, (n, v) in zip(bars, data):
        _bar_label(ax, bar, v)
    ax.set_ylim(0.66, 0.73)
    _style(ax)
    ax.set_title("合成三院场景 · 三甲/县/社区 4200/2400/1100 例\n特征缺失率 5%-22%",
                 fontsize=12.5, color=INK, pad=10)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "01_auc_dual.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


def chart_convergence():
    """图2 联邦收敛曲线（UCI 真实数据 13 轮）"""
    rounds = np.arange(0, 13)
    aucs = [0.5, 0.9069, 0.9076, 0.9076, 0.9084, 0.9084, 0.9076,
            0.9076, 0.9091, 0.9091, 0.9091, 0.9091, 0.9091]
    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=300)
    ax.plot(rounds, aucs, color=BLUE, linewidth=2.8, marker="o", markersize=6,
            markerfacecolor=BLUE, markeredgecolor="white", zorder=4,
            label="联邦 FedAvg（数据不出院）")
    ax.axhline(0.9019, color=ORANGE, linewidth=1.8, linestyle="--", zorder=3,
               label="集中训练上界 0.9019（数据大池化，现实不可行）")
    ax.fill_between(rounds, aucs, 0.88, color=BLUE, alpha=0.08, zorder=2)
    ax.annotate("第 2 轮即达 0.9069，快速收敛", xy=(2, 0.9069), xytext=(3.2, 0.895),
                color=GOLD, fontsize=12, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=GOLD, lw=1.4))
    ax.annotate("稳定 0.9091", xy=(11, 0.9091), xytext=(8.4, 0.921),
                color=GREEN, fontsize=12, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.4))
    ax.set_ylim(0.88, 0.925)
    _style(ax)
    ax.xaxis.grid(False)
    ax.set_xlabel("联邦训练轮次", color=INK, fontsize=12)
    ax.set_ylabel("全局测试 AUC", color=INK, fontsize=12)
    ax.set_title("UCI 真实患者数据联邦收敛曲线 · 12 轮稳定收敛 · 单机 CPU 可复现",
                 fontsize=14, fontweight="bold", color=INK, pad=12)
    ax.legend(loc="lower right", fontsize=11, frameon=False)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "02_convergence.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


def chart_architecture():
    """图3 四层架构图（浅色）"""
    fig, ax = plt.subplots(figsize=(12, 6.6), dpi=300)
    fig.patch.set_facecolor("white")
    ax.axis("off")
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 7.6)
    layers = [
        (6.0, BLUE, "应用层", "9 个医疗智能体 · 数据消费方与民生出口",
         "健康卫士 · 影像卫士 · 政策参谋 · 泛癌卫士（温附医 Cell 2026 Oncoformer）· 档案管家 · 数据管家 · 报销助手 · 药事卫士 · EEG 面板"),
        (4.35, NAVY, "流通层", "数据要素市场化闭环",
         "数据产品目录 → 用途限定授权 → 交易结算（支付宝 live 已实证）→ 收益分成 70/20/10"),
        (2.7, ORANGE, "协作层", "数据可用不可见",
         "联邦学习 FedAvg + 差分隐私（数据不出院） · AI 病历治理 Copilot（qwen3:4b 院内网 · PHI 脱敏 · 自动降级）"),
        (1.05, GOLD, "合规层", "全程可监管",
         "审计存证链 sha256 串联 · 监管看板实时可见 · 对齐《数据安全法》《个保法》《数据二十条》· GB/T 39725"),
    ]
    for y, color, name, subtitle, detail in layers:
        ax.add_patch(plt.Rectangle((0.4, y), 11.0, 1.45,
                                   facecolor="#F8FAFC", edgecolor=color,
                                   linewidth=2.2, zorder=3))
        ax.text(0.7, y + 1.05, name, color=color, fontsize=17,
                fontweight="bold", va="center", zorder=4)
        ax.text(2.5, y + 1.05, subtitle, color=INK, fontsize=13.5,
                va="center", zorder=4)
        ax.text(0.7, y + 0.42, detail, color=SUB, fontsize=10.5,
                va="center", zorder=4)
    ax.annotate("", xy=(11.75, 7.45), xytext=(11.75, 1.05),
                arrowprops=dict(arrowstyle="-|>", color=BLUE, lw=2.0))
    ax.text(11.55, 4.25, "交\n易\n反\n哺\n数\n据", color=BLUE, fontsize=11,
            ha="center", va="center", fontweight="bold")
    ax.set_title("瓯医数链四层架构 · 治理 - 协作 - 流通 - 监管完整闭环",
                 fontsize=15, fontweight="bold", color=INK, pad=14)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "03_architecture.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


def chart_payment_loop():
    """图4 支付宝真实收款闭环实证（2026-09-02 终验时间轴）"""
    fig, ax = plt.subplots(figsize=(12, 4.6), dpi=300)
    ax.axis("off")
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4.6)
    steps = [
        ("下单", "Agent 服务订单\n¥3.90 深度画像", "0:00", NAVY),
        ("生产收银台", "支付宝 live 模式\nexcashier 直达", "0:02", BLUE),
        ("实付完成", "第三方扫码\n真实交易", "4:18", ORANGE),
        ("落账回写", "pending → paid\n交易号回写", "4:40", GREEN),
        ("存证上链", "sha256 存证哈希\n管理端对账可见", "4:42", GOLD),
    ]
    ax.plot([0.7, 11.3], [2.6, 2.6], color="#CBD5E1", linewidth=2.2, zorder=2)
    xs = [1.4, 3.65, 5.9, 8.15, 10.4]
    for x, (name, detail, t, color) in zip(xs, steps):
        ax.scatter([x], [2.6], s=300, color=color, zorder=4,
                   edgecolor="white", linewidth=2.5)
        ax.text(x, 3.35, name, color=color, fontsize=14, fontweight="bold",
                ha="center")
        ax.text(x, 2.05, detail, color=SUB, fontsize=10, ha="center", va="top")
        ax.text(x, 3.85, t, color=INK, fontsize=11, ha="center",
                fontweight="bold")
    ax.text(6, 0.55,
            "订单 OM260902105109807F · 交易号 2026090222001435791410985789 · 下单到落账全程 4 分 42 秒",
            color=INK, fontsize=11.5, ha="center", fontweight="bold")
    ax.set_title("已实证的真实收款闭环 · 2026-09-02 终验 · 每笔交易自动存证上链",
                 fontsize=14, fontweight="bold", color=INK, pad=12)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "04_payment_loop.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


def chart_loadtest():
    """图5 LLM 全链路并发压测"""
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8), dpi=300)
    fig.suptitle("LLM 全链路并发压测：验收档与 2 倍压力档均 100% 成功（真实 API 调用）",
                 fontsize=14.5, fontweight="bold", color=INK, y=1.02)

    ax = axes[0]
    data = [("并发 5 路\n(验收档)", 15 / 15, BLUE), ("并发 10 路\n(2倍压力档)", 20 / 20, GREEN)]
    bars = ax.bar([d[0] for d in data], [d[1] for d in data],
                  color=[d[2] for d in data], width=0.45, zorder=3)
    for bar, (_, v, _) in zip(bars, data):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{v:.0%}", ha="center", va="bottom", color=INK,
                fontsize=14, fontweight="bold")
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    _style(ax)
    ax.set_title("请求成功率 35/35", fontsize=12.5, color=INK, pad=10)

    ax = axes[1]
    labels = ["p50", "p90", "p95"]
    c5 = [14.0, 23.2, 24.3]
    c10 = [15.0, 28.8, 28.9]
    x = np.arange(3)
    w = 0.32
    ax.bar(x - w / 2, c5, w, color=BLUE, label="并发 5 路", zorder=3)
    ax.bar(x + w / 2, c10, w, color="#93B4E0", label="并发 10 路", zorder=3)
    for xi, v in zip(x - w / 2, c5):
        ax.text(xi, v + 0.5, f"{v:.1f}s", ha="center", fontsize=10, color=INK)
    for xi, v in zip(x + w / 2, c10):
        ax.text(xi, v + 0.5, f"{v:.1f}s", ha="center", fontsize=10, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    _style(ax)
    ax.set_ylabel("端到端延迟（秒）", color=INK, fontsize=12)
    ax.set_title("延迟来自真实推理（框架开销 <35ms）", fontsize=12.5,
                 color=INK, pad=10)
    ax.legend(fontsize=11, frameon=False)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "05_loadtest.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


def chart_revenue():
    """图6 商业模式：四大收入源"""
    fig, ax = plt.subplots(figsize=(11, 5.6), dpi=300)
    ax.axis("off")
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 5.6)
    items = [
        ("模型 API 订阅", "联邦模型按年授权 ¥8 万/年起（目录已上架）", "40%", BLUE, 4.5),
        ("平台交易佣金", "数据产品交易额 20%（分成架构已内置）", "30%", NAVY, 3.3),
        ("治理服务费", "病历治理与数据资产化服务（按数据集计价）", "18%", GOLD, 2.1),
        ("监管 SaaS", "卫健/数据局合规看板年费", "12%", GREEN, 0.9),
    ]
    for i, (name, detail, pct, color, y) in enumerate(items):
        ax.add_patch(plt.Rectangle((0.4, y), 10.2, 1.0,
                                   facecolor="#F8FAFC", edgecolor=color,
                                   linewidth=1.8, zorder=3))
        ax.add_patch(plt.Rectangle((0.4, y), 0.18, 1.0, facecolor=color,
                                   edgecolor="none", zorder=4))
        ax.text(0.85, y + 0.5, name, color=color, fontsize=14.5,
                fontweight="bold", va="center", zorder=4)
        ax.text(3.3, y + 0.5, detail, color=SUB, fontsize=11.5,
                va="center", zorder=4)
        ax.text(9.6, y + 0.5, pct, color=color, fontsize=16,
                fontweight="bold", va="center", ha="right", zorder=4)
    ax.set_title("四大收入源 · 目标客户：区域医共体 / 卫健·数据主管部门 / 保险精算与药研 CRO",
                 fontsize=13.5, fontweight="bold", color=INK, pad=12)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "06_revenue.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


def chart_sharing():
    """图7 收益分成 70/20/10"""
    fig, ax = plt.subplots(figsize=(7.2, 5.6), dpi=300)
    data = [("医院 70%", 70, BLUE), ("平台 20%", 20, ORANGE),
            ("数据贡献者 10%", 10, GOLD)]
    labels = [d[0] for d in data]
    sizes = [d[1] for d in data]
    colors = [d[2] for d in data]
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors, startangle=90,
        textprops={"color": INK, "fontsize": 13, "fontweight": "bold"},
        wedgeprops={"edgecolor": "white", "linewidth": 3},
        labeldistance=1.12, autopct="%d%%", pctdistance=0.75,
        explode=[0.03, 0, 0])
    for t in autotexts:
        t.set_color("white")
        t.set_fontweight("bold")
        t.set_fontsize(13)
    centre = plt.Circle((0, 0), 0.52, fc="white")
    ax.add_artist(centre)
    ax.text(0, 0.06, "收益分成", ha="center", va="center",
            color=BLUE, fontsize=17, fontweight="bold")
    ax.text(0, -0.16, "交易自动结算", ha="center", va="center",
            color=SUB, fontsize=12)
    ax.set_title("医院得大头：让基层医院从数据要素中真正获益",
                 fontsize=13, fontweight="bold", color=INK, pad=10)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "07_sharing.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


def chart_timeline():
    """图8 温州落地路线图"""
    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=300)
    ax.axis("off")
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.2)
    stages = [
        ("T+3月", "落地", "入驻中国（温州）智能谷\n注册项目公司\n申报鹿城 AI+医疗示范项目库", BLUE),
        ("T+6月", "试点", "1 家三甲 + 2 家基层机构签约\n真实医共体环境联邦建模试点", NAVY),
        ("T+12月", "运营", "数据产品目录上线运营\n首笔真实数据要素交易\n申报省标杆项目（最高500万奖补）", ORANGE),
        ("T+24月", "规模化", "复制浙南医共体\n建成区域医疗数据要素\n流通基础设施", GOLD),
    ]
    ax.plot([0.8, 11.2], [2.7, 2.7], color="#CBD5E1", linewidth=2.2, zorder=2)
    xs = [1.7, 4.3, 7.0, 9.8]
    for x, (t, name, detail, color) in zip(xs, stages):
        ax.scatter([x], [2.7], s=280, color=color, zorder=4,
                   edgecolor="white", linewidth=2.5)
        ax.text(x, 3.25, t, color=color, fontsize=15, fontweight="bold",
                ha="center")
        ax.text(x, 3.9, name, color=INK, fontsize=14, fontweight="bold",
                ha="center")
        ax.text(x, 2.25, detail, color=SUB, fontsize=10.5, ha="center",
                va="top")
    ax.annotate("", xy=(11.6, 2.7), xytext=(10.9, 2.7),
                arrowprops=dict(arrowstyle="-|>", color=BLUE, lw=2.2))
    ax.set_title("温州落地路线图 · 鹿城起笔 · 浙南成网",
                 fontsize=15, fontweight="bold", color=INK, pad=12)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "08_timeline.png"), dpi=300,
                bbox_inches="tight", facecolor="white")
    plt.close()


if __name__ == "__main__":
    for fn in (chart_auc_dual, chart_convergence, chart_architecture,
               chart_payment_loop, chart_loadtest, chart_revenue,
               chart_sharing, chart_timeline):
        fn()
        print("ok", fn.__name__)
