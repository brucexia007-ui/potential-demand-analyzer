# 部署指南

本文区分“从源码启动的本地环境”和“使用固定镜像摘要的生产环境”。两者不要混用。

## 本地源码环境

### 前置要求

- Docker Desktop 或 Docker Engine
- Docker Compose v2
- 至少一个 OpenAI 兼容 LLM API
- Python 3（仅用于生成配置加密密钥）

### 1. 创建本地配置

```bash
cp .env.example .env
```

生成 Fernet 密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

编辑 `.env`，至少设置：

```ini
ENV=development
CONFIG_ENCRYPTION_KEY=<上一步生成的值>
ADMIN_PASSWORD=<本地管理员密码>

LLM_PROVIDER_PRIMARY_BASE_URL=https://your-provider.example/v1
LLM_PROVIDER_PRIMARY_API_KEY=replace-with-your-key
LLM_PROVIDER_PRIMARY_MODELS=replace-with-model-name
DEFAULT_MODEL=replace-with-model-name
```

搜索 Provider 可以在 `.env` 中配置，也可以首次登录后通过设置向导写入。未配置付费搜索 API 时，系统会保留 DuckDuckGo 兜底，但其可用性和结果质量不作保证。

### 2. 构建并启动

```bash
docker compose \
  --project-name potential-demand-local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  up -d --build
```

唯一 Web 入口：<http://127.0.0.1:3001>

默认用户名为 `admin`，密码是 `.env` 中的 `ADMIN_PASSWORD`。本地覆盖层取消固定容器名、使用项目隔离的 PostgreSQL 卷，并发布 Backend、Redis、PostgreSQL 和 Browserless 调试端口。

如需改变入口端口：

```bash
LOCAL_HTTP_PORT=3100 docker compose \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  up -d
```

Windows PowerShell：

```powershell
$env:LOCAL_HTTP_PORT = "3100"
docker compose --project-name potential-demand-local -f docker-compose.yml -f docker-compose.local.yml up -d
```

可配置端口：

| 变量 | 默认值 | 服务 |
| --- | ---: | --- |
| `LOCAL_HTTP_PORT` | 3001 | 唯一 Web 入口 |
| `LOCAL_BACKEND_PORT` | 8000 | Backend 调试端口 |
| `LOCAL_BROWSERLESS_PORT` | 3002 | Browserless 调试端口 |
| `LOCAL_REDIS_PORT` | 6380 | Redis 调试端口 |
| `LOCAL_POSTGRES_PORT` | 5436 | PostgreSQL 调试端口 |

### 3. 验证

```bash
docker compose \
  --project-name potential-demand-local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  ps
```

```bash
curl http://127.0.0.1:3001/health
```

预期所有长期服务为 `running`，带健康检查的核心服务为 `healthy`，健康端点返回成功状态。

### 4. 日志与停止

```bash
docker compose --project-name potential-demand-local -f docker-compose.yml -f docker-compose.local.yml logs -f
```

```bash
docker compose --project-name potential-demand-local -f docker-compose.yml -f docker-compose.local.yml down
```

删除卷会永久移除本地数据库、快照和配置，仅在明确需要重置环境时执行：

```bash
docker compose --project-name potential-demand-local -f docker-compose.yml -f docker-compose.local.yml down -v
```

## 本地非 Docker 开发

先通过基础 Compose 启动 PostgreSQL 和 Redis：

```bash
docker compose up -d postgres redis
```

后端：

```bash
cd backend
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

宿主机连接配置：

```ini
DATABASE_URL=postgresql+psycopg2://demand_user:demand_pass@localhost:5436/demand_analyzer
REDIS_URL=redis://localhost:6380/0
```

Worker：

```bash
cd backend
celery -A app.worker.celery_app.celery_app worker --loglevel=INFO --concurrency=2
```

前端：

```bash
cd frontend
npm ci
npm run dev
```

开发服务器默认运行在 <http://localhost:3000>。

## 生产环境

生产覆盖层不从本地源码构建镜像，而是强制使用发布流水线生成的不可变镜像摘要。这样可以保证部署内容与测试通过的制品一致。

### 1. 准备镜像

推送符合 `v*` 的 Git 标签后，`.github/workflows/deploy.yml` 会：

1. 运行完整后端和前端验证；
2. 构建 Backend 与 Frontend 镜像；
3. 推送到 GitHub Container Registry；
4. 在工作流摘要中输出镜像 digest。

### 2. 创建生产配置

```bash
cp .env.production.example .env.production
```

必须替换以下类别的值：

- `BACKEND_IMAGE`、`FRONTEND_IMAGE`、`NGINX_IMAGE`、`REDIS_IMAGE`、`CERTBOT_IMAGE` 的固定 digest；
- `SECRET_KEY`、`CONFIG_ENCRYPTION_KEY`、管理员、PostgreSQL、Redis 和 Browserless 强密码；
- 数据库和 Redis 连接串；
- LLM、embedding 与搜索 Provider；
- 对外域名、CORS 和 TLS 证书路径。

生产预检会拒绝示例值、弱密码、无效 Fernet 密钥、非 TLS Cookie、未固定镜像和其他不安全配置。

### 3. TLS 证书

将证书放入 `deploy/certs/`，并确保 `.env.production` 中的容器路径与实际挂载一致：

```ini
TLS_ENABLED=true
TLS_CERT_PATH=/etc/nginx/certs/fullchain.pem
TLS_KEY_PATH=/etc/nginx/certs/privkey.pem
```

`deploy/certs/` 已被 Git 忽略。不要提交私钥。

### 4. 启动生产栈

```bash
docker compose --env-file .env.production \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d
```

默认唯一入口为 <https://127.0.0.1:10443>；可通过 `APP_HTTPS_PORT` 修改宿主机端口。Backend、Frontend、PostgreSQL、Redis 和 Browserless 不向宿主机发布端口。

### 5. 上线检查

```bash
docker compose --env-file .env.production \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  ps
```

至少验证：

- `/health` 和 `/ready`；
- 登录、首次配置、新建任务和主体澄清；
- 实时状态与轮询兜底；
- 报告、证据审计、PDF/Word 导出和历史回查；
- Worker、Crawler、Beat、Outbox Relay 的健康状态；
- LLM、搜索和 Browserless 对公网的真实连通性；
- 数据库备份和恢复演练。

## 常见问题

### 配置保存失败

确认 `CONFIG_ENCRYPTION_KEY` 是有效 Fernet 密钥，并且 Backend、Worker、Crawler 使用相同值。

### Worker 无法连接 Redis

- 容器内：`redis://redis:6379/0`
- 本地进程：`redis://localhost:6380/0`
- 生产环境启用密码后：连接串必须包含 URL 编码后的密码

### 搜索或抓取全部失败

依次检查搜索 Provider 健康状态、容器 DNS、Docker/EternalNetwork 代理规则、SSRF 公网地址校验以及目标站点的反爬限制。不要通过关闭生产安全校验来绕过网络问题。

### PDF 导出失败

检查 Backend 镜像中的 WeasyPrint 系统依赖和中文字体，并查看后端日志中的具体渲染错误。

### 迁移或数据初始化失败

查看 Backend 启动日志和数据库连接；不要直接删除生产卷。生产故障应先备份，再执行经过评审的迁移或恢复流程。
