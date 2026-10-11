"""等保三级·数据备份脚本（P0-1.2）：SQLite 在线备份 + 完整性校验 + 轮转保留。

用法（在 backend/ 目录下运行）：
    python scripts/backup_db.py                     # 备份默认库 ./yibao.db → ./backups/
    python scripts/backup_db.py --keep 14           # 保留最近 14 份，超期自动清理
    python scripts/backup_db.py --db other.db --out /path/to/backups
    python scripts/backup_db.py --verify            # 备份后执行 PRAGMA integrity_check

备份方式：sqlite3 原生 Connection.backup()（在线备份 API）——备份期间源库可
正常读写，WAL 模式安全，不锁业务。备份文件为完整 .db 快照，可用 sqlite3 直接打开。

恢复演练（等保三级验收要求"备份可恢复"）：
    1. 停止后端服务（或确认无写入）；
    2. 用备份文件替换源库：copy /y backups/yibao_YYYYMMDD_HHMMSS.db yibao.db
       （Windows）或 cp backups/yibao_*.db yibao.db（Linux）；
    3. 重启服务，访问 /api/health/detailed 确认 database available；
    4. 抽查业务数据（如 GET /api/users/user_001/profile）确认完整。
    建议每季度演练一次并留存演练记录。

生产部署建议：crontab 每日备份（0 2 * * * cd /app/backend && python scripts/backup_db.py
--keep 30 --verify），备份目录挂载独立存储卷/离线副本（等保三级要求异地备份）。
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def backup(db_path: Path, out_dir: Path, keep: int = 0, verify: bool = False) -> Path:
    """执行一次在线备份，返回备份文件路径。"""
    if not db_path.exists():
        print(f"[备份失败] 源数据库不存在：{db_path}")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = out_dir / f"{db_path.stem}_{stamp}.db"

    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)  # 在线备份 API：不锁库、WAL 安全
            if verify:
                ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    print(f"[备份失败] 完整性校验未通过：{ok}")
                    sys.exit(1)
                print("[校验] PRAGMA integrity_check = ok")
        finally:
            dst.close()
    finally:
        src.close()

    size_mb = backup_path.stat().st_size / 1024 / 1024
    print(f"[备份完成] {backup_path}（{size_mb:.2f} MB）")

    if keep > 0:
        backups = sorted(out_dir.glob(f"{db_path.stem}_*.db"))
        for old in backups[:-keep]:
            old.unlink()
            print(f"[轮转] 清理超期备份：{old.name}")
    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(description="瓯医数链 数据库在线备份（等保三级·数据备份）")
    parser.add_argument("--db", default="./yibao.db", help="源数据库路径（默认 ./yibao.db）")
    parser.add_argument("--out", default="./backups", help="备份目录（默认 ./backups）")
    parser.add_argument("--keep", type=int, default=30,
                        help="保留最近 N 份备份，0 = 不轮转（默认 30）")
    parser.add_argument("--verify", action="store_true",
                        help="备份后执行 PRAGMA integrity_check 完整性校验")
    args = parser.parse_args()

    backup(Path(args.db), Path(args.out), keep=args.keep, verify=args.verify)


if __name__ == "__main__":
    main()
