# 潜在需求分析系统

面向 B2B 销售、售前与商业情报团队的 AI 研究平台。系统先由 LLM Research Director 构建分析目标、问题树和研究任务 DAG，再按获批查询执行搜索、抓取、证据提取、准入、补检与商业裁决，最终输出可追溯的商机分析报告。

当前内置的重点领域 Skill 是“客服中心商机分析”，覆盖信创改造、客服智能化、呼叫平台、IP 电话、服务体验、BPO、现有厂商锁定与竞争态势。

> 本项目输出的是基于公开证据的辅助研究结果，不构成投资、法律、合规或采购建议。报告中的未知项、推断和待核验项必须与事实分开使用。

## 核心能力

- **LLM 主导研究规划**：LLM 负责定义商业目标、递归问题树、任务 DAG、来源策略、精确查询、完成条件和停止条件；平台只做主体绑定、权限、预算和契约校验。
- **耐久研究执行**：经校验的计划物化为可重入 WorkUnit，支持并行任务、依赖控制、暂停、继续、取消、断点恢复和最多一轮证据缺口补检。
- **证据纪律**：区分外部事实、系统推断和待核验项，支持来源分级、时间衰减、主体归属、反证、重复证据治理和证据链回溯。
- **领域 Skill 体系**：`SKILL.md`、`references/`、测试用例和版本化数据契约共同驱动运行时；内置客服中心一级 Skill 与多个专项二级 Skill。
- **受控 Field Agent**：在合规护栏下执行公开网页体验探针；遇到验证码、登录墙或访问限制时停止并降级为被动证据研究。
- **商业裁决与作战报告**：报告回答“是否值得投入、卖什么、为什么现在、如何赢、下一步行动和停止条件”，并提供 OIG 等级、战卡、竞争态势和证据索引。
- **完整产品流程**：自然语言建任务、主体澄清、批量任务、实时状态、能力中心、配置中心、历史回查，以及 PDF/Word 导出。

## 研究执行逻辑

```text
用户需求
  ↓
主体预检与必要澄清
  ↓
LLM Research Director：目标树 + 任务 DAG + 精确查询
  ↓
平台契约校验与计划快照
  ↓
搜索 → 筛选 → 抓取 → 提取 → 证据准入
  ↓
证据充分性检查 ──缺口且预算允许──→ LLM 追加一次补检计划
  ↓
领域评估 → OIG 裁决 → 商业报告 Composer
  ↓
报告、证据审计、PDF/Word 导出与历史回查
```

Query Compiler 只编译和执行 LLM 已获批准的查询，不固定生成研究方向，也不会静默改写搜索语义。详细设计见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Next.js 15、React 19、TypeScript、Tailwind CSS |
| 后端 | FastAPI、Pydantic、SQLAlchemy、Alembic |
| 异步执行 | Celery、Redis、PostgreSQL 耐久 WorkUnit |
| AI | OpenAI 兼容接口、LiteLLM、LangGraph |
| 搜索与抓取 | 博查、DuckDuckGo、Bing/Tavily 配置接口、HTTP 抓取、Browserless/Playwright |
| 报告 | Markdown、WeasyPrint PDF、python-docx |
| 部署 | Docker Compose、Nginx、TLS、PostgreSQL 16/pgvector |

## 快速开始

### 前置条件

- Docker Desktop 或 Docker Engine
- Docker Compose v2
- 至少一个 OpenAI 兼容的 LLM API
- 推荐配置一个搜索 API；未配置时可使用 DuckDuckGo 兜底，但稳定性较低

### 1. 准备配置

```bash
cp .env.example .env
```

在 `.env` 中至少设置：

```ini
ENV=development
CONFIG_ENCRYPTION_KEY=<Fernet key>
ADMIN_PASSWORD=<仅用于本地开发的管理员密码>

LLM_PROVIDER_PRIMARY_BASE_URL=https://your-provider.example/v1
LLM_PROVIDER_PRIMARY_API_KEY=replace-with-your-key
LLM_PROVIDER_PRIMARY_MODELS=replace-with-model-name
DEFAULT_MODEL=replace-with-model-name

SEARCH_PROVIDER=bocha
BOCHA_API_KEY=replace-with-your-search-key
```

生成 Fernet 密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. 从源码启动本地栈

```bash
docker compose \
  --project-name potential-demand-local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  up -d --build
```

打开 <http://127.0.0.1:3001>。首次登录账号为 `admin`，密码取自 `.env` 中的 `ADMIN_PASSWORD`；随后按首次设置向导配置并测试 LLM、搜索、抓取与预算。

检查状态：

```bash
docker compose \
  --project-name potential-demand-local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  ps
```

`docker-compose.local.yml` 会取消固定容器名并使用项目隔离的数据卷；如默认端口冲突，可设置 `LOCAL_HTTP_PORT`、`LOCAL_BACKEND_PORT`、`LOCAL_BROWSERLESS_PORT`、`LOCAL_REDIS_PORT` 和 `LOCAL_POSTGRES_PORT`。

> `.env`、`.env.production`、运行日志、导出报告和浏览器测试产物均已加入 `.gitignore`，请勿提交真实密钥或客户私有数据。

### 3. 生产部署

生产部署必须使用固定镜像摘要、强密码、TLS 和生产预检：

```bash
cp .env.production.example .env.production
```

完成全部必填项后：

```bash
docker compose --env-file .env.production \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d
```

默认唯一入口是 <https://127.0.0.1:10443>。完整步骤和安全门禁见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 本地开发与测试

### 后端

```bash
cd backend
python -m pip install -r requirements.txt
python -m pytest --tb=short
```

后端测试需要 PostgreSQL 与 Redis；测试配置参考 [CI 工作流](.github/workflows/ci.yml)。

### 前端

```bash
cd frontend
npm ci
npm run build
```

真实栈 E2E：

```bash
cd frontend
npm run test:e2e:real
```

E2E 需要已启动的候选栈和本地测试账号，具体变量见 `frontend/playwright.real.config.ts`。

## 项目结构

```text
backend/
  app/
    research_planning/   # LLM Research Director、计划契约与持久化
    execution/           # 耐久执行、WorkUnit 与状态机
    evidence/            # 证据提取、准入、审计与快照
    skills/              # Skill 编译、运行时目录与 evaluation
    worker/              # Celery 执行链、补检与报告编排
    tools/               # 搜索、抓取、导出和安全校验
  data/skills/           # 内置 SKILL.md、references 与黄金用例
  data/poc/              # 公开网页证据的去标识化研究测试集
  tests/                 # 后端自动化测试
frontend/
  src/app/               # Next.js App Router 页面和业务组件
  e2e-real/              # 真实候选栈 E2E
deploy/                  # Nginx、TLS、备份和部署脚本
docs/                    # PRD、架构决策、POC 与验证记录
```

## 内置客服中心 Skill

一级 Skill：`backend/data/skills/analyzing-contact-center-opportunities/`

主要专项能力包括：

- 客服中心能力版图与成熟度
- 信创、智能化与招采转型信号
- 服务体验审计
- BPO 与服务外包模式
- 现有厂商锁定和竞争脆弱度
- 能力缺口、产品匹配与 OIG 商机裁决

`references/` 中包含能力分类、时间衰减、触发事件、来源路由、商机规则、可观测性和报告契约。新增或修改 Skill 时必须同步更新黄金用例。

## 安全、合规与数据边界

- 不要提交 API Key、数据库凭据、Cookie、客户私有材料或带身份信息的体验数据。
- Field Agent 仅用于公开渠道；不得绕过验证码、登录墙、访问控制或站点限制。
- 公开投诉、评论和网页证据只能作为弱信号，不得单独推导当前采购需求。
- 对外使用报告前，应由业务人员核验关键结论、主体归属、时间窗口和来源授权。
- 安全问题请遵循 [SECURITY.md](SECURITY.md)，不要在公开 Issue 中披露漏洞细节。

## 贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。公开网页证据、第三方商标与外部内容仍归各自权利人所有。
