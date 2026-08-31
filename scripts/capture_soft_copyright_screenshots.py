# -*- coding: utf-8 -*-
"""软著申请材料界面截图脚本：访问本地前端 http://localhost:3100 批量截图

用法：python scripts/capture_soft_copyright_screenshots.py
输出：docs/screenshots/*.png（视口 1440x900，2x 高清，PNG）
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://localhost:3100"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "screenshots")

results = []  # (filename, size_bytes, status, note)


def log(msg):
    print(msg, flush=True)


def take_shot(page, filename, full=True):
    path = os.path.join(OUT, filename)
    page.screenshot(path=path, full_page=full)
    size = os.path.getsize(path)
    results.append((filename, size, "OK", ""))
    log(f"[OK] {filename}  {size / 1024:.0f} KB")


def fail_and_shot(page, filename, err, full=True):
    """等待超时/失败时，截取当前状态作为兜底"""
    try:
        path = os.path.join(OUT, filename)
        page.screenshot(path=path, full_page=full)
        size = os.path.getsize(path)
        results.append((filename, size, "PARTIAL", str(err)[:150]))
        log(f"[PARTIAL] {filename}  {size / 1024:.0f} KB  原因: {str(err)[:150]}")
    except Exception as e2:  # noqa: BLE001
        results.append((filename, 0, "FAIL", str(e2)[:150]))
        log(f"[FAIL] {filename}  {str(e2)[:150]}")


def settle(page, seconds=1.5):
    """等待网络空闲 + 动画稳定"""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(seconds)


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            locale="zh-CN",
        )
        page = ctx.new_page()
        page.set_default_timeout(60000)

        # ---------- 01 联邦协作网络-数据全景 ----------
        try:
            page.goto(f"{BASE}/federation", wait_until="domcontentloaded", timeout=90000)
            page.wait_for_selector("text=再入院率", timeout=90000)  # 三家医院数据全景卡片
            settle(page, 2)
            take_shot(page, "01-联邦协作网络-数据全景.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "01-联邦协作网络-数据全景.png", e)

        # ---------- 02 联邦协作网络-训练结果 ----------
        try:
            page.get_by_role("button", name="发起联邦训练").click()
            page.wait_for_selector("text=联邦模型全局 AUC", timeout=90000)
            # 等两张 ECharts（AUC 收敛曲线 + 逐院公平性）canvas 渲染完成
            page.wait_for_function("document.querySelectorAll('canvas').length >= 2", timeout=30000)
            time.sleep(2.5)  # ECharts 入场动画
            take_shot(page, "02-联邦协作网络-训练结果.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "02-联邦协作网络-训练结果.png", e)

        # ---------- 03 AI病历治理-输入 ----------
        try:
            page.goto(f"{BASE}/governance", wait_until="domcontentloaded", timeout=90000)
            settle(page, 1)
            page.get_by_role("button", name="填入示例病历").click()
            page.wait_for_function(
                "document.querySelector('textarea') && document.querySelector('textarea').value.length > 50",
                timeout=15000,
            )
            time.sleep(0.8)
            take_shot(page, "03-AI病历治理-输入.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "03-AI病历治理-输入.png", e)

        # ---------- 04 AI病历治理-脱敏对比（本地推理 10-60 秒，超时 60s 截当前状态） ----------
        try:
            page.get_by_role("button", name="一键治理").click()
            page.wait_for_selector("text=识别敏感实体", timeout=60000)
            time.sleep(1.5)
            take_shot(page, "04-AI病历治理-脱敏对比.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "04-AI病历治理-脱敏对比.png", f"等待脱敏结果超时(60s)，截取当前状态: {e}")

        # ---------- 05 数据要素市场-产品目录 ----------
        try:
            page.goto(f"{BASE}/marketplace", wait_until="domcontentloaded", timeout=90000)
            page.wait_for_selector("text=件在售", timeout=90000)  # 产品目录已加载
            settle(page, 2)
            take_shot(page, "05-数据要素市场-产品目录.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "05-数据要素市场-产品目录.png", e)

        # ---------- 06 数据要素市场-监管看板（滚动到看板区域，视口聚焦截图） ----------
        try:
            page.wait_for_selector("text=监管方看板", timeout=30000)
            page.evaluate(
                """() => {
                    const el = [...document.querySelectorAll('h2')]
                        .find(e => (e.textContent || '').includes('监管方看板'));
                    if (el) {
                        const r = el.getBoundingClientRect();
                        window.scrollTo(0, Math.max(0, r.top + window.scrollY - 24));
                    }
                }"""
            )
            time.sleep(2)  # ECharts 渲染稳定
            take_shot(page, "06-数据要素市场-监管看板.png", full=False)
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "06-数据要素市场-监管看板.png", e, full=True)

        # ---------- 07 平台首页 ----------
        try:
            page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=90000)
            settle(page, 2.5)
            take_shot(page, "07-平台首页.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "07-平台首页.png", e)

        # ---------- 08 可信数据空间 ----------
        try:
            page.goto(f"{BASE}/security/data-space", wait_until="domcontentloaded", timeout=90000)
            settle(page, 2.5)
            take_shot(page, "08-可信数据空间.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "08-可信数据空间.png", e)

        # ---------- 09 登录页 ----------
        try:
            page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=90000)
            settle(page, 1.5)
            take_shot(page, "09-登录页.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "09-登录页.png", e)

        # ---------- 10 管理员面板（未登录状态展示登录入口，不执行登录） ----------
        try:
            page.goto(f"{BASE}/admin", wait_until="domcontentloaded", timeout=90000)
            try:
                page.wait_for_selector("text=超级管理员登录", timeout=20000)
            except Exception:  # noqa: BLE001
                pass  # 若已有会话直接展示面板
            settle(page, 1.5)
            take_shot(page, "10-管理员面板.png")
        except Exception as e:  # noqa: BLE001
            fail_and_shot(page, "10-管理员面板.png", e)

        browser.close()

    # ---------- 汇总 ----------
    log("\n========== 截图汇总 ==========")
    ok = 0
    for filename, size, status, note in results:
        log(f"{status:8s} {filename:40s} {size / 1024:8.0f} KB  {note}")
        if status == "OK":
            ok += 1
    log(f"共 {len(results)} 张，完全成功 {ok} 张")


if __name__ == "__main__":
    main()
