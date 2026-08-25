#!/bin/sh
# 容器启动：建表 → （空库时）导种子 → 起服务。
# set -e：迁移或种子真出错就让容器起不来，不要带着半个库对外服务。
set -e

echo "[entrypoint] alembic upgrade head"
python -m alembic upgrade head

if [ "${ZY_AUTO_SEED:-1}" = "1" ]; then
  echo "[entrypoint] seed（库里已有资产则跳过）"
  python scripts/seed.py --skip-if-seeded
else
  echo "[entrypoint] ZY_AUTO_SEED=0，跳过种子导入"
fi

# exec：uvicorn 接管 PID 1，docker stop 的 SIGTERM 才能送到它手上
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
