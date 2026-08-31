# -*- coding: utf-8 -*-
"""瓯医数链初赛 PPT 数据图表生成（真实数据口径 2026-08-31）"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "PPT素材", "charts")
os.makedirs(OUT, exist_ok=True)

BG = "#0A1A3F"
CYAN = "#00D4FF"
ORANGE = "#FF6B35"
GOLD = "#FFD700"
GREEN = "#00C853"


def dark_ax(fig, ax):
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)


def style_spines(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("bottom", "left"):
        ax.spines[s].set_color("#3A4E7A")
    ax.tick_params(colors="#C9D6EE", labelsize=13)


def chart_auc_real():
    data = [
        ("A机构·老年层\n本地模型", 0.8362, "#5B7BC0"),
        ("B机构·中年层\n本地模型", 0.8427, "#5B7BC0"),
        ("C机构·青年层\n本地模型", 0.9351, "#5B7BC0"),
        ("联邦学习\n数据不出院", 0.9091, CYAN),
        ("集中训练上界\n(现实不可行)", 0.9019, ORANGE),
    ]
    fig, ax = plt.subplots(figsize=(11, 6), dpi=300)
    dark_ax(fig, ax)
    labels = [d[0] for d in data]
    vals = [d[1] for d in data]
    colors = [d[2] for d in data]
    bars = ax.bar(labels, vals, color=colors, width=0.55, edgecolor="none", zorder=3)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.008,
                f"{v:.4f}", ha="center", va="bottom", color="#FFFFFF",
                fontsize=15, fontweight="bold", zorder=4)
    ax.set_ylim(0.78, 0.97)
    ax.yaxis.grid(True, color="#22335C", linestyle="--", alpha=0.8)
    ax.set_axisbelow(True)
    style_spines(ax)
    ax.set_ylabel("全局测试 AUC", color="#C9D6EE", fontsize=14)
    ax.set_title("UCI 心脏病真实队列 · 297 例真实患者 · 非 IID 三机构",
                 color=CYAN, fontsize=17, fontweight="bold", pad=18)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "01_auc_real.png"), dpi=300,
                bbox_inches="tight", facecolor=BG)
    plt.close()


def chart_convergence():
    rounds = np.arange(1, 13)
    aucs = [0.5, 0.9069, 0.9076, 0.9076, 0.9084, 0.9084, 0.9076,
            0.9076, 0.9091, 0.9091, 0.9091, 0.9091]
    fig, ax = plt.subplots(figsize=(11, 6), dpi=300)
    dark_ax(fig, ax)
    ax.plot(rounds, aucs, color=CYAN, linewidth=3, marker="o",
            markersize=7, markerfacecolor=CYAN, markeredgecolor=BG, zorder=4,
            label="联邦 FedAvg（含差分隐私）")
    ax.axhline(0.9019, color=ORANGE, linewidth=2, linestyle="--", zorder=3,
               label="集中训练上界 0.9019")
    ax.fill_between(rounds, aucs, 0.88, color=CYAN, alpha=0.12, zorder=2)
    ax.annotate("第 2 轮即达 0.9069\n快速收敛", xy=(2, 0.9069), xytext=(3.2, 0.895),
                color=GOLD, fontsize=14, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=GOLD, lw=1.5))
    ax.annotate("稳定追平上界", xy=(11, 0.9091), xytext=(8.2, 0.925),
                color=GREEN, fontsize=14, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=GREEN, lw=1.5))
    ax.set_ylim(0.88, 0.93)
    ax.yaxis.grid(True, color="#22335C", linestyle="--", alpha=0.8)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    style_spines(ax)
    ax.set_xlabel("联邦训练轮次", color="#C9D6EE", fontsize=14)
    ax.set_ylabel("全局测试 AUC", color="#C9D6EE", fontsize=14)
    ax.set_title("联邦收敛曲线 · 12 轮稳定收敛 · 单机 CPU 即可复现",
                 color=CYAN, fontsize=17, fontweight="bold", pad=18)
    ax.legend(loc="lower right", fontsize=13, facecolor=BG,
              edgecolor="#3A4E7A", labelcolor="#FFFFFF")
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "02_convergence.png"), dpi=300,
                bbox_inches="tight", facecolor=BG)
    plt.close()


def chart_sharing():
    data = [
        ("医院\n70%", 70, CYAN),
        ("平台\n20%", 20, ORANGE),
        ("数据贡献者\n10%", 10, GOLD),
    ]
    fig, ax = plt.subplots(figsize=(8, 8), dpi=300)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    labels = [d[0] for d in data]
    sizes = [d[1] for d in data]
    colors = [d[2] for d in data]
    ax.pie(sizes, labels=labels, colors=colors, startangle=90,
           textprops={"color": "#FFFFFF", "fontsize": 16, "fontweight": "bold"},
           wedgeprops={"edgecolor": BG, "linewidth": 3},
           labeldistance=1.12)
    centre = plt.Circle((0, 0), 0.58, fc=BG)
    ax.add_artist(centre)
    ax.text(0, 0.08, "收益分成", ha="center", va="center",
            color=CYAN, fontsize=20, fontweight="bold")
    ax.text(0, -0.14, "自动结算", ha="center", va="center",
            color="#C9D6EE", fontsize=15)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "03_sharing.png"), dpi=300,
                bbox_inches="tight", facecolor=BG)
    plt.close()


def chart_revenue():
    data = [
        ("模型 API 订阅\n¥8万/年起", 40, CYAN),
        ("平台交易佣金\n20%抽成", 30, ORANGE),
        ("治理服务费\n按数据集计价", 18, GOLD),
        ("监管 SaaS\n合规看板年费", 12, GREEN),
    ]
    fig, ax = plt.subplots(figsize=(8, 8), dpi=300)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    labels = [d[0] for d in data]
    sizes = [d[1] for d in data]
    colors = [d[2] for d in data]
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors, startangle=90,
        textprops={"color": "#FFFFFF", "fontsize": 15, "fontweight": "bold"},
        wedgeprops={"edgecolor": BG, "linewidth": 3},
        labeldistance=1.15,
        autopct="%d%%", pctdistance=0.75,
        explode=[0.04, 0, 0, 0])
    for t in autotexts:
        t.set_color("#0A1A3F")
        t.set_fontweight("bold")
        t.set_fontsize(15)
    centre = plt.Circle((0, 0), 0.52, fc=BG)
    ax.add_artist(centre)
    ax.text(0, 0.05, "四大收入源", ha="center", va="center",
            color=CYAN, fontsize=19, fontweight="bold")
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "04_revenue.png"), dpi=300,
                bbox_inches="tight", facecolor=BG)
    plt.close()


def chart_architecture():
    fig, ax = plt.subplots(figsize=(12, 7), dpi=300)
    dark_ax(fig, ax)
    ax.axis("off")
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    layers = [
        (6.1, CYAN, "应用层", "9 个医疗智能体 · 数据消费方与民生出口",
         "健康卫士 · 影像卫士 · 政策参谋 · 泛癌卫士(Cell 2026) · 档案管家 · 数据管家 · 报销助手 · 药事卫士 · EEG 面板"),
        (4.5, "#1E5BFF", "流通层", "数据要素市场化闭环",
         "数据产品目录 → 用途限定授权 → 交易结算 → 收益分成 70/20/10 → 浙江大数据交易中心"),
        (2.9, ORANGE, "协作层", "数据可用不可见",
         "联邦学习 FedAvg+DP（数据不出院） · AI 病历治理 Copilot（qwen3:4b 院内网 · PHI 脱敏 · LLM 失效自动降级）"),
        (1.3, GOLD, "合规层", "全程可监管",
         "差分隐私 · 审计存证链 sha256 串联 · 监管看板实时可见 · 对齐数据安全法/个保法/数据二十条"),
    ]
    for y, color, name, subtitle, detail in layers:
        ax.add_patch(plt.Rectangle((0.4, y), 11.2, 1.45,
                                   facecolor="#12234E", edgecolor=color,
                                   linewidth=2.5, zorder=3, alpha=0.95))
        ax.text(0.75, y + 1.05, name, color=color, fontsize=19,
                fontweight="bold", va="center", zorder=4)
        ax.text(2.6, y + 1.05, subtitle, color="#FFFFFF", fontsize=15,
                va="center", zorder=4)
        ax.text(0.75, y + 0.42, detail, color="#A9BADF", fontsize=11.5,
                va="center", zorder=4)
    ax.annotate("", xy=(11.85, 7.55), xytext=(11.85, 1.3),
                arrowprops=dict(arrowstyle="-|>", color=CYAN, lw=2.2))
    ax.text(11.62, 4.4, "交\n易\n反\n哺\n数\n据", color=CYAN, fontsize=12,
            ha="center", va="center", fontweight="bold")
    ax.set_title("瓯医数链四层架构 · 治理-协作-流通-监管完整闭环",
                 color=CYAN, fontsize=18, fontweight="bold", pad=15)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "05_architecture.png"), dpi=300,
                bbox_inches="tight", facecolor=BG)
    plt.close()


def chart_audit_chain():
    fig, ax = plt.subplots(figsize=(12, 4.5), dpi=300)
    dark_ax(fig, ax)
    ax.axis("off")
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 4.5)
    nodes = [
        ("联邦任务", "#1E5BFF"),
        ("治理产物", "#1E5BFF"),
        ("授权交易", "#1E5BFF"),
        ("收益结算", "#1E5BFF"),
        ("监管看板", GOLD),
    ]
    x0, w, gap = 0.4, 1.9, 0.45
    for i, (name, color) in enumerate(nodes):
        x = x0 + i * (w + gap)
        ax.add_patch(plt.Rectangle((x, 1.5), w, 1.3, facecolor="#12234E",
                                   edgecolor=color, linewidth=2.2, zorder=3))
        ax.text(x + w / 2, 2.15, name, color="#FFFFFF", fontsize=15,
                fontweight="bold", ha="center", va="center", zorder=4)
        ax.text(x + w / 2, 1.15, f"sha256 #{i+1:04d}", color="#7C90BC",
                fontsize=10.5, ha="center", zorder=4)
        if i < len(nodes) - 1:
            ax.annotate("", xy=(x + w + gap - 0.06, 2.15), xytext=(x + w + 0.06, 2.15),
                        arrowprops=dict(arrowstyle="-|>", color=CYAN, lw=2))
    ax.text(6, 3.55, "每个事件摘要哈希串联上链 · 任何篡改导致链条断裂 · 监管方一键校验",
            color=CYAN, fontsize=14.5, fontweight="bold", ha="center")
    ax.text(6, 0.55, "隐私事件实时监控：0 起 · 全程留痕满足等保三级审计要求（留存 ≥ 6 个月）",
            color="#A9BADF", fontsize=13, ha="center")
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "06_audit_chain.png"), dpi=300,
                bbox_inches="tight", facecolor=BG)
    plt.close()


def chart_timeline():
    fig, ax = plt.subplots(figsize=(12, 5.5), dpi=300)
    dark_ax(fig, ax)
    ax.axis("off")
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.5)
    stages = [
        ("T+3月", "落地", "入驻温州智能谷\n注册项目公司\n申报AI+医疗示范库", CYAN),
        ("T+6月", "试点", "1 家三甲 + 2 家基层\n签约联邦建模试点\n真实医共体环境", "#1E5BFF"),
        ("T+12月", "运营", "数据产品目录上线\n首笔真实要素交易\n申报省标杆(500万奖补)", ORANGE),
        ("T+24月", "规模化", "复制浙南医共体\n建成区域流通基础设施", GOLD),
    ]
    ax.plot([0.8, 11.2], [2.8, 2.8], color="#3A4E7A", linewidth=2.5, zorder=2)
    xs = [1.6, 4.2, 6.9, 9.7]
    for x, (t, name, detail, color) in zip(xs, stages):
        ax.scatter([x], [2.8], s=260, color=color, zorder=4,
                   edgecolor=BG, linewidth=2)
        ax.text(x, 3.35, t, color=color, fontsize=17, fontweight="bold",
                ha="center")
        ax.text(x, 4.0, name, color="#FFFFFF", fontsize=15, fontweight="bold",
                ha="center")
        ax.text(x, 1.85, detail, color="#A9BADF", fontsize=11.5, ha="center",
                va="top")
    ax.annotate("", xy=(11.6, 2.8), xytext=(10.9, 2.8),
                arrowprops=dict(arrowstyle="-|>", color=CYAN, lw=2.5))
    ax.set_title("温州落地路线图 · 鹿城起笔 · 浙南成网",
                 color=CYAN, fontsize=18, fontweight="bold", pad=12)
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, "07_timeline.png"), dpi=300,
                bbox_inches="tight", facecolor=BG)
    plt.close()


if __name__ == "__main__":
    for fn in (chart_auc_real, chart_convergence, chart_sharing,
               chart_revenue, chart_architecture, chart_audit_chain,
               chart_timeline):
        fn()
        print("ok", fn.__name__)
