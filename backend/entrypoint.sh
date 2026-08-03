#!/bin/bash
# ========================================
# Docker 启动脚本：迁移 → 种子数据 → 启动应用
# ========================================
set -e

python -m app.core.production_preflight

if [ "${RUN_DB_BOOTSTRAP:-false}" = "true" ]; then
    echo "==> 运行数据库迁移..."
    alembic upgrade head

    echo "==> 检查并初始化默认数据..."
    python -c "from app.db.init_data import init_default_user, seed_search_providers, sync_system_skills; init_default_user(); seed_search_providers(); sync_system_skills()"
else
    echo "==> 跳过数据库迁移与种子初始化（非数据库启动所有者）"
fi

echo "==> 启动应用..."
exec "$@"
