#!/bin/bash
# ============================================================
# 瓯医数链 魔搭创空间入口
# - 后端 FastAPI   → 127.0.0.1:8000（内部）
# - 前端 Next.js   → 0.0.0.0:7860（对外，魔搭强制端口）
# - /api/* 由 Next rewrites 代理到本地 8000
# ============================================================
set -e
# 管道中任一命令失败即整体失败：防止 tee 吞掉 init_db.py 的非零退出码
# （曾导致线上 init_db.py 失败被静默掩盖，/api/users 返回空数组）
set -o pipefail

# 强制 UTF-8：init_db.py/后端日志含 emoji 与中文，容器 locale 异常时 GBK/ASCII 编码会导致
# UnicodeEncodeError 崩溃（Windows GBK 环境已实证），而崩溃发生在插入事务内会回滚 users 表
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# 默认环境变量（优先保留平台注入的 variables/secrets，仅缺省时使用默认值）
export DEMO_OFFLINE="${DEMO_OFFLINE:-true}"
export YIBAO_SESSION_SECRET="${YIBAO_SESSION_SECRET:-oumed-modelscope-demo-secret-change-me}"

# 持久化目录（魔搭 /mnt/workspace 挂载点，重启不丢）
# 探测可写性：挂载异常时降级到容器内路径（数据不持久，但服务可用、容器不崩溃）
PERSIST_DIR=/mnt/workspace
if ! (mkdir -p "$PERSIST_DIR/data" "$PERSIST_DIR/chroma_data" \
      && touch "$PERSIST_DIR/data/.writable" 2>/dev/null \
      && rm -f "$PERSIST_DIR/data/.writable"); then
  echo "[entrypoint] ⚠️ /mnt/workspace 不可写，降级到容器内路径（重启会丢数据）"
  PERSIST_DIR=/app/var
  mkdir -p "$PERSIST_DIR/data" "$PERSIST_DIR/chroma_data"
fi
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///$PERSIST_DIR/data/yibao.db}"
export CHROMA_PERSIST_DIR="${CHROMA_PERSIST_DIR:-$PERSIST_DIR/chroma_data}"

# 知识库索引播种：workspace 索引无数据时从镜像 seed 拷贝（幂等，重启不重复拷贝）
# ⚠️ 判定不能用“chroma.sqlite3 是否存在”（PersistentClient 初始化即建库文件），
# 也不能只看 collections 表行数（空 collection 也占一行，曾误判跳过播种）。
# 必须查 embeddings 表行数：>0 才算有有效索引。
if [ -d /app/chroma_seed ]; then
  need_seed=1
  if [ -f "$PERSIST_DIR/chroma_data/chroma.sqlite3" ] && \
     python -c "import sqlite3,sys; con=sqlite3.connect('$PERSIST_DIR/chroma_data/chroma.sqlite3'); sys.exit(0 if con.execute('select count(*) from embeddings').fetchone()[0] > 0 else 1)" 2>/dev/null; then
    need_seed=0
    echo "[entrypoint] 知识库索引已存在，跳过播种"
  fi
  if [ "$need_seed" = "1" ]; then
    rm -rf "$PERSIST_DIR"/chroma_data/*
    cp -r /app/chroma_seed/. "$PERSIST_DIR/chroma_data/"
    echo "[entrypoint] 已播种知识库索引 seed -> $PERSIST_DIR/chroma_data"
  fi
fi

# 数据库初始化（幂等：users 表已有数据则跳过插入，参考 backend/scripts/init_db.py）
# - init_db.py 默认读取 /app/data/mock_data.json（Dockerfile 已 COPY data/ → /app/data/）
# - DATABASE_URL 指向持久化目录（重启不丢）
# - pipefail 已启用，python 失败不会被 tee 掩盖；失败不阻塞启动，由下方校验兜底
run_init_db() {
  (cd /app/backend && python scripts/init_db.py) 2>&1 | tee /tmp/init_db.log
}

# 查询 users 表行数（仅支持 SQLite；非 SQLite 返回 -2，出错返回 -1）
count_db_users() {
  python - <<'PY' 2>/dev/null || echo "-1"
import os
url = os.environ.get("DATABASE_URL", "")
if not url.startswith("sqlite"):
    print("-2")
else:
    path = url.split("://", 1)[1]
    if path.startswith("//"):  # ///相对路径 vs ////绝对路径
        path = path[1:]
    try:
        import sqlite3
        con = sqlite3.connect(path)
        print(con.execute("select count(*) from users").fetchone()[0])
    except Exception:
        print("-1")
PY
}

echo "[entrypoint] 初始化数据库（幂等）..."
if ! run_init_db; then
  echo "[entrypoint] ⚠️ init_db.py 首次执行失败，日志尾部："
  tail -n 30 /tmp/init_db.log 2>/dev/null || true
  echo "[entrypoint] 重试初始化..."
  run_init_db || echo "[entrypoint] ⚠️ init_db.py 重试仍失败（后端 lifespan 会建空表兜底）"
fi

# 启动前校验：users 表必须有演示用户（线上 /api/users 与多用户切换依赖它）
# users>0 即代表 init_db.py 全量事务（用户/缴费/就诊/购药/EEG）已成功落库
DB_USER_COUNT=$(count_db_users)
if [ "$DB_USER_COUNT" = "0" ] || [ "$DB_USER_COUNT" = "-1" ]; then
  echo "[entrypoint] ⚠️ users 表为空（count=$DB_USER_COUNT），重试初始化..."
  run_init_db >/dev/null 2>&1 || true
  DB_USER_COUNT=$(count_db_users)
fi
if [ "$DB_USER_COUNT" = "-2" ]; then
  echo "[entrypoint] 非 SQLite 数据库（$DATABASE_URL），跳过本地数据校验"
elif [ "$DB_USER_COUNT" -gt 0 ] 2>/dev/null; then
  echo "[entrypoint] ✅ 数据库就绪：users 表 $DB_USER_COUNT 条记录"
else
  echo "[entrypoint] ❌ users 表最终为空（count=$DB_USER_COUNT）：/api/users 将返回空数组，"
  echo "[entrypoint]    多用户切换将依赖前端 mock 兜底；完整日志见 /tmp/init_db.log"
  tail -n 30 /tmp/init_db.log 2>/dev/null || true
fi

echo "[entrypoint] 启动后端 uvicorn :8000 ..."
cd /app/backend
nohup python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    --workers 1 \
    > /tmp/backend.log 2>&1 &
BACKEND_PID=$!

# 实时输出后端日志到 stdout（魔搭日志能看到 LLM 调用错误、SQL 警告等）
tail -f /tmp/backend.log 2>/dev/null &
TAIL_PID=$!

# 等待后端就绪（最多 90s）
for i in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "[entrypoint] 后端就绪 ($i s)"
    break
  fi
  sleep 1
done

# 后端异常退出时终止容器
trap 'echo "[entrypoint] 后端进程已退出，关闭容器"; kill $BACKEND_PID 2>/dev/null || true' EXIT

echo "[entrypoint] 启动前端 node server.js :7860 ..."
cd /app/frontend
export PORT=7860
export HOSTNAME=0.0.0.0
exec node server.js
