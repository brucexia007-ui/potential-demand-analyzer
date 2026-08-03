# API 概览

后端使用 FastAPI。接口模型、必填字段和响应结构以运行实例生成的 OpenAPI 为权威来源：

- 本地开发 Swagger UI：<http://127.0.0.1:8000/docs>
- 本地开发 OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>
- 生产环境：业务 API 统一通过唯一站点入口的 `/api/` 访问

除登录、健康检查和少数初始化状态接口外，业务接口通常要求有效的 HttpOnly Cookie 会话。浏览器客户端应使用同源请求并携带凭据。

## 健康检查

```http
GET /health
GET /ready
```

- `/health`：进程存活检查。
- `/ready`：数据库、Redis 和必要运行条件的就绪检查。

生产 Nginx 会直接代理这两个端点。

## 认证

```http
POST /api/auth/login
POST /api/auth/refresh
GET  /api/auth/me
POST /api/auth/logout
```

登录成功后由服务端设置会话 Cookie。生产环境必须启用 HTTPS-only Cookie。

## 任务与研究执行

```http
GET  /api/tasks
POST /api/tasks
GET  /api/tasks/{task_id}
GET  /api/tasks/{task_id}/logs
GET  /api/tasks/{task_id}/research-plan

GET  /api/tasks/{task_id}/execution
GET  /api/tasks/{task_id}/execution/events
GET  /api/tasks/{task_id}/execution/events/stream
GET  /api/tasks/{task_id}/research-status/events
GET  /api/tasks/{task_id}/research-status/events/stream
POST /api/tasks/{task_id}/pause
POST /api/tasks/{task_id}/resume
POST /api/tasks/{task_id}/cancel
```

任务可能在执行前或报告前进入澄清状态。调用方应同时消费 SSE，并保留周期性状态查询兜底；不要只依赖单次页面加载状态。

### 创建任务示意

任务支持公司、需求方向、行业、区域、研究深度、输出画像和运行时 Skill 等字段。准确契约请查看 OpenAPI。

```http
POST /api/tasks
Content-Type: application/json

{
  "company_name": "示例企业",
  "demand_direction": "客服中心智能化与信创改造",
  "industry": "金融",
  "region": "上海",
  "research_depth": "standard",
  "runtime_skill_slug": "analyzing-contact-center-opportunities"
}
```

不要在公开 Issue、日志或测试夹具中使用真实客户私有数据。

## 报告与证据

```http
GET /api/reports/{task_id}
GET /api/reports/{task_id}/evidences
GET /api/reports/{task_id}/pdf
GET /api/reports/{task_id}/docx
```

报告响应包含 Markdown、业务裁决和证据索引。证据中的事实、推断和待核验状态具有不同语义，不应只按展示文本解析。

导出端点返回二进制附件：

- PDF：`application/pdf`
- Word：`application/vnd.openxmlformats-officedocument.wordprocessingml.document`

## Field Agent

```http
GET /api/tasks/{task_id}/field-agent-runs
```

该接口只展示受控公开体验审计的运行记录。Field Agent 遇到验证码、登录墙或访问限制时会停止或降级，不提供绕过能力。

## 批量任务

主要接口包括：

```http
POST /api/batches
GET  /api/batches
GET  /api/batches/{batch_id}
GET  /api/batches/{batch_id}/summary
POST /api/batches/{batch_id}/pause
POST /api/batches/{batch_id}/resume
POST /api/batches/{batch_id}/cancel
POST /api/batches/{batch_id}/retry-failed
POST /api/batches/{batch_id}/export
```

批量导入还提供模板、预览、校验、dry-run 和创建接口。字段映射与错误结构以 OpenAPI 为准。

## 配置中心

配置接口覆盖：

- 系统就绪状态与首次设置完成；
- LLM Provider、模型路由与连接测试；
- 搜索 Provider 与健康测试；
- 预算、抓取、安全和数据留存；
- 配置导入、导出与全量测试。

所有 API Key 在服务端加密存储，列表响应只能返回掩码，不得返回明文。

## Skill 与能力中心

Skill、产品能力、目标客户、商机、证据声明和报告工作区分别由对应路由模块提供。由于这些契约迭代较快，集成方应基于固定版本的 `/openapi.json` 生成客户端，不要依赖本文手写字段。

## 错误处理

FastAPI 标准错误通常使用：

```json
{
  "detail": "错误说明"
}
```

调用方至少处理：

- `400/422`：请求或业务契约无效；
- `401/403`：未登录或无权限；
- `404`：任务、报告或资源不存在；
- `409`：状态冲突；
- `429`：限流；
- `5xx`：依赖故障或服务异常。

研究任务中的搜索失败、证据不足或报告 `PARTIAL` 不一定对应 HTTP 失败；应同时读取任务终态、阶段日志和质量门禁结果。
