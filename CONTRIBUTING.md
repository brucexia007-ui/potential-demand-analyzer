# 贡献指南

感谢你为潜在需求分析系统贡献代码、领域规则、测试或文档。

## 开始之前

1. 先搜索现有 Issue，确认问题或提案没有重复。
2. 对较大功能、数据模型变化、新外部依赖或新的主动网页交互方式，先提交设计 Issue 讨论边界。
3. 安全漏洞不要提交公开 Issue，请按 [SECURITY.md](SECURITY.md) 私下报告。
4. 贡献即表示你有权提交相关代码、文档和数据，并同意其按本项目 MIT 许可证发布。

## 开发环境

推荐使用 Docker Compose：

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

配置要求和首次启动步骤见 [README.md](README.md) 与 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 后端

```bash
cd backend
python -m pip install -r requirements.txt
python -m pytest --tb=short
```

### 前端

```bash
cd frontend
npm ci
node --test tests/security-dependencies.test.mjs
npm run build
```

真实栈 E2E 需要本地候选栈和测试账号：

```bash
cd frontend
npm run test:e2e:real
```

## 变更原则

- 保持事实、推断和待核验项分离；不要为提高报告完整度而伪造证据。
- LLM 负责目标、任务和查询语义；平台校验器不得静默新增或改写研究方向。
- 新增或修改 Skill 时，同步维护 `SKILL.md`、必要的 `references/`、数据契约和黄金用例。
- 网页抓取与 Field Agent 必须遵守频率限制、数据最小化、验证码/登录墙退出和 SSRF 防护。
- 不提交真实密钥、Cookie、客户私有数据、个人身份信息、构建产物或真实 E2E 登录状态。
- 不使用大段第三方网页全文作为测试夹具；只保留可验证算法所需的最小片段和来源信息。
- 不引入没有实际需求的兼容层、双轨实现或静默降级路径。

## 测试要求

Bug 修复应先增加能稳定复现问题的测试，再提交修复。Pull Request 至少说明：

- 问题或目标；
- 根因和设计取舍；
- 修改范围；
- 已运行的测试与结果；
- 边缘情况、已知限制和人工验证步骤。

按变更范围选择测试：

| 变更 | 最低验证 |
| --- | --- |
| 后端纯逻辑 | 对应单元测试 + 相关集成测试 |
| 数据库模型/迁移 | 迁移测试 + PostgreSQL 集成测试 |
| Research Director/执行链 | 契约测试 + Worker/WorkUnit 回归 |
| Skill/规则/模板 | Skill 编译、evaluation 与黄金用例 |
| 前端组件/页面 | 构建 + 对应交互测试 |
| 登录、设置、任务、报告或导出主路径 | 真实栈 E2E |
| Compose/部署/安全配置 | `docker compose config` + 候选栈健康检查 |

## 提交与 Pull Request

- 从最新默认分支创建主题分支。
- 一个 PR 聚焦一个可审查的目标，避免混入无关格式化或生成文件。
- 提交信息使用简短祈使句，例如 `fix: preserve planning contract errors`。
- PR 描述使用真实 Markdown，关联 Issue，并附测试证据。
- 修改用户界面时附截图；修改报告格式时附去标识化样例。
- CI 全部通过且评审意见处理完毕后再合并。

## 文档与数据

- 用户可见行为、配置项和部署方式变化时同步更新 README 或部署文档。
- POC 数据规则见 [backend/data/poc/README.md](backend/data/poc/README.md)。
- 不要在 Issue、PR、日志或截图中暴露客户名称与内部数据，除非它们本来就是经过确认的公开测试样本。
