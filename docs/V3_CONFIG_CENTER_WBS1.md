# V3 配置中心 WBS-1 完成报告

## 概述

WBS-1 是 v3.0 升级的第一阶段，目标是为配置中心建立底层数据模型与加密存储基础。本阶段只做数据层和服务层，**不修改任何运行时逻辑**。

## 完成内容

### WBS-1.1：数据库模型与 Alembic 迁移

**新增 5 张数据库表：**

| 表名 | 用途 | 主键类型 |
|------|------|----------|
| `settings` | 通用配置键值存储 | Integer 自增 |
| `llm_providers` | LLM Provider 配置 | Integer 自增 |
| `search_providers` | 搜索 Provider 配置 | Integer 自增 |
| `model_routes` | Agent 角色 → 模型路由 | Integer 自增 |
| `provider_health` | Provider 健康状态（WBS-4 启用） | Integer 自增 |

**迁移文件：** `backend/migrations/versions/003_add_config_center.py`
- 父迁移：002_add_batches
- 所有默认值使用 PostgreSQL server_default
- 包含完整 downgrade() 支持

**ORM Model：** 追加到 `backend/app/db/models.py`

### WBS-1.2：ConfigEncryptionService

**文件：** `backend/app/config_center/encryption.py`

- 使用 `cryptography.fernet.Fernet` 对称加密（AES-128-CBC + HMAC-SHA256）
- 密钥从环境变量 `CONFIG_ENCRYPTION_KEY` 读取
- 未配置时抛出 `EncryptionKeyNotConfiguredError`（含明确提示）
- `encrypt_secret()` / `decrypt_secret()` / `mask_secret()` 三个核心函数
- 脱敏规则：≤8 字符 → `****`，>8 字符 → `前4位****后4位`

**生成密钥命令：**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### WBS-1.3：LLM Provider 配置服务

**文件：** `backend/app/config_center/provider_config.py`

- `create_provider()` — 创建时自动加密 API Key
- `update_provider()` — 不传 key 保留旧值，更新字段可选
- `list_providers()` — 返回脱敏列表（含 `masked_api_key`）
- `get_provider()` — 返回脱敏单条，不存在返回 None
- `delete_provider()` — 删除并返回成功/失败
- `mask_provider()` — ORM → 安全 dict

**不返回的字段：** `api_key`, `api_key_encrypted`

### WBS-1.4：Search Provider 配置服务

**文件：** `backend/app/config_center/search_config.py`

- 支持 provider_type：`bocha`, `bing`, `tavily`, `duckduckgo`, `custom`
- DuckDuckGo 免 API Key 可直接保存
- 接口设计同 LLM Provider 服务
- 非法 provider_type 在创建/更新时抛出 ValueError

### WBS-1.5：配置状态检查 API

**API：** `GET /api/config/status`（无需认证）

**返回示例：**
```json
{
  "configured": false,
  "llm_provider_configured": true,
  "search_provider_configured": false,
  "model_routes_configured": false,
  "missing_items": ["search_provider"]
}
```

**判断规则：**
- `llm_provider_configured`：至少 1 个 enabled=true 的 LLM Provider
- `search_provider_configured`：至少 1 个 enabled=true 的 Search Provider
- `model_routes_configured`：至少 1 条 model_route（仅信息展示，不强制）
- `configured`：llm + search 均配置

### WBS-1.6：测试与文档

**测试统计：** 72 条测试用例
- 40 条单元测试通过（无需 DB）
- 32 条 DB 集成测试可在 PostgreSQL 可用时运行

## 文件变更清单

### 新增文件（13 个）

| 文件 | 说明 |
|------|------|
| `backend/app/config_center/__init__.py` | 模块初始化 |
| `backend/app/config_center/encryption.py` | 加密服务 |
| `backend/app/config_center/provider_config.py` | LLM Provider 配置服务 |
| `backend/app/config_center/search_config.py` | Search Provider 配置服务 |
| `backend/app/config_center/status.py` | 配置状态检查逻辑 |
| `backend/app/api/config_routes.py` | 配置 API 路由 |
| `backend/migrations/versions/003_add_config_center.py` | Alembic 迁移 |
| `backend/tests/test_config_center_models.py` | 模型测试（23 条） |
| `backend/tests/test_encryption.py` | 加密测试（19 条） |
| `backend/tests/test_provider_config.py` | LLM Provider 服务测试（13 条） |
| `backend/tests/test_search_config.py` | Search Provider 服务测试（11 条） |
| `backend/tests/test_config_status_api.py` | 配置状态测试（6 条） |
| `docs/V3_CONFIG_CENTER_WBS1.md` | 本文档 |

### 修改文件（2 个）

| 文件 | 改动 |
|------|------|
| `backend/app/db/models.py` | 追加 5 个 ORM Model（Setting, LLMProvider, SearchProvider, ModelRoute, ProviderHealth） |
| `backend/main.py` | 注册 config_router |

### 未修改的禁止修改文件 ✅

- `backend/app/llm/gateway_client.py` ✅ 未修改
- `backend/app/tools/search_client.py` ✅ 未修改
- `backend/app/llm/model_router.py` ✅ 未修改
- `backend/app/api/routes.py` ✅ 未修改
- `backend/app/api/model_settings.py` ✅ 未修改
- `backend/app/worker/harness_worker.py` ✅ 未修改
- `backend/app/worker/celery_app.py` ✅ 未修改
- `backend/app/agents/` 所有文件 ✅ 未修改
- 所有前端文件 ✅ 未修改

## 本阶段不做的内容（留给后续 WBS）

- GatewayClient 的 DB 配置读取 → WBS-2
- SearchClient 的 DB 配置读取 → WBS-2
- ModelRouter 的 DB 路由 → WBS-2
- Provider 连接测试 API → WBS-3
- Setup Wizard 前端 → WBS-3
- 429 自适应并发 → WBS-4
- Provider 熔断 → WBS-4

## 后续 WBS-2 接入点

当 WBS-2 实现 RuntimeConfigLoader 时：

1. **GatewayClient** 可通过 `provider_config.list_providers()` 获取 LLM Provider 列表，替代当前的 `_load_providers_from_env()`
2. **SearchClient** 可通过 `search_config.list_search_providers()` 获取搜索 Provider 列表
3. **ModelRouter** 可通过 `model_routes` 表读取路由配置
4. **解密 API Key** 使用 `encryption.decrypt_secret()` 把 `api_key_encrypted` 还原为明文

## 回滚方法

```bash
# 回滚迁移
cd backend
alembic downgrade 002

# 恢复 main.py（如需要）
git checkout backend/main.py
```

## 测试命令

```bash
# WBS-1 全部测试
cd backend
python -m pytest tests/test_config_center_*.py tests/test_encryption.py -v

# 验证不破坏现有测试
python -m pytest tests/test_model_router.py tests/test_harness.py -v
```
