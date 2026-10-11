"""联邦参数信封密钥轮换工具（P1.2 节点认证与传输安全）。

触发场景：密钥疑似泄露 / 定期轮换（等保要求）/ 节点退出需隔离历史参数。

轮换语义（P1.2 验收核心）：
- 信封格式 enc2:<key_id>:nonce:ct 自带密钥版本号，平台密钥环
  FEDERATION_PARAM_KEYS 支持多版本共存，最后一个为活跃写入密钥；
- 过渡期：平台保留旧密钥（解密在途信封）+ 节点全部切换到新密钥；
- 轮换完成：从密钥环移除旧密钥并销毁 → 其 key_id 不在环中，
  历史密文永久不可解密（泄露密钥也解不开已归档密文——旧密文只存密文信封）。

本脚本只做密钥生成与操作指引输出，不直接改服务器配置（配置由环境变量/
部署系统管理，避免脚本绕过变更流程）。

用法（在 backend/ 目录下运行）：
    python scripts/rotate_federation_key.py                     # 从环境变量读当前密钥
    python scripts/rotate_federation_key.py --keys "kid:key,…"  # 显式给当前密钥环
    python scripts/rotate_federation_key.py --out rotation.md   # 结果同时写入文件留档
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.crypto import parse_key  # noqa: E402


def key_id(key: bytes) -> str:
    """与平台 federation_coordinator.envelope_key_id / 节点侧同算法。"""
    return hashlib.sha256(key).hexdigest()[:8]


def parse_current_ring(raw_keys: str | None) -> dict[str, bytes]:
    """解析当前密钥环：优先 --keys 参数，其次环境变量 FEDERATION_PARAM_KEYS，
    再回退单密钥 FEDERATION_PARAM_KEY（与平台 keyring() 同规则）。"""
    raw = (raw_keys or settings.FEDERATION_PARAM_KEYS or "").strip()
    ring: dict[str, bytes] = {}
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if ":" not in part:
                sys.exit(f"[失败] 密钥环条目格式非法（应为 key_id:key）: {part[:8]}…")
            kid, k = part.split(":", 1)
            key = parse_key(k.strip())
            if not key:
                sys.exit("[失败] 密钥环条目密钥为空")
            ring[kid.strip()] = key
        return ring
    single = parse_key(settings.FEDERATION_PARAM_KEY)
    return {key_id(single): single} if single else {}


def rotate(ring: dict[str, bytes]) -> tuple[str, str, str]:
    """生成新密钥并返回 (过渡期配置, 完成期配置, 节点侧指引密钥)。"""
    new_key = os.urandom(32)
    new_b64 = base64.b64encode(new_key).decode("ascii")
    new_kid = key_id(new_key)

    # 过渡期：旧密钥在前（保留解密在途信封），新密钥在最后（活跃写入）
    entries = [f"{kid}:{base64.b64encode(k).decode('ascii')}" for kid, k in ring.items()]
    entries.append(f"{new_kid}:{new_b64}")
    transition = ",".join(entries)
    # 完成期：仅保留新密钥（旧密钥销毁 → 历史密文永久不可解密）
    final = f"{new_kid}:{new_b64}"
    return transition, final, new_b64


def main() -> None:
    ap = argparse.ArgumentParser(description="联邦参数信封密钥轮换（P1.2）")
    ap.add_argument("--keys", default=None, help="当前密钥环（缺省读环境变量）")
    ap.add_argument("--out", default=None, help="将轮换结果额外写入文件留档（md）")
    args = ap.parse_args()

    ring = parse_current_ring(args.keys)
    if not ring:
        sys.exit("[失败] 当前未配置任何联邦密钥（FEDERATION_PARAM_KEYS / FEDERATION_PARAM_KEY 均为空），"
                 "首次启用请直接配置密钥而非轮换")

    transition, final, new_b64 = rotate(ring)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# 联邦参数信封密钥轮换结果（瓯医数链 P1.2）",
        f"",
        f"- 轮换时间：{now}",
        f"- 当前密钥环：{len(ring)} 把（{', '.join(ring)}）",
        f"- 新密钥 key_id：{key_id(parse_key(new_b64))}",
        "",
        "## 第 1 步 · 平台过渡期配置（旧密钥保留解密在途信封，新密钥活跃写入）",
        "",
        "```bash",
        f"export FEDERATION_PARAM_KEYS=\"{transition}\"",
        "# 清空/删除单密钥变量，避免旧口径残留：",
        "unset FEDERATION_PARAM_KEY",
        "```",
        "重启平台后端，确认 /api/health 正常。",
        "",
        "## 第 2 步 · 各医院节点同步切换（逐节点）",
        "",
        "```bash",
        "# 节点环境变量更新为新密钥（平台活跃密钥）后重启节点 Agent：",
        f"export FEDERATION_PARAM_KEY=\"{new_b64}\"",
        "```",
        "验证：任一节点完成一轮参数上传且平台聚合成功（管理端任务详情轮次推进）。",
        "",
        "## 第 3 步 · 完成轮换（确认无在途任务后执行）",
        "",
        "```bash",
        f"export FEDERATION_PARAM_KEYS=\"{final}\"",
        "```",
        "旧密钥自此销毁：其 key_id 不再在密钥环中，历史密文（distributed_job_updates.",
        "payload_enc 归档）永久不可解密——即使泄露的旧密钥被攻击者持有也无效。",
        "",
        "## 操作纪律",
        "",
        "- 新密钥经密钥管理通道分发，不要出现在聊天/邮件/代码仓库；",
        "- 过渡期尽量短（建议 ≤1 个任务周期）；",
        "- 在变更管理台账登记本次轮换（时间/触发原因/执行人/新 key_id）。",
    ]
    text = "\n".join(lines)
    print(text)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n[已留档] {args.out}")


if __name__ == "__main__":
    main()
