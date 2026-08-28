# Phase 3～6 实现与证据审计

审计基线：`3fb1b2ffcdc9f907cb96c30d67b7ebc952fa6462`

审计时间：2026-08-28（Asia/Shanghai）

结论：**NO-GO**。Phase 3～5 的源代码与自动化契约已经实现并通过；Phase 6 的在线/离线候选构建、供应链门禁、单机三轮快照复原验收和草稿后公开 GitHub Release 编排已实现，但 Tag 工作流尚未在真实 GitHub 环境执行，一台 Windows 11 的三轮完全断网验收也没有运行证据。因此不能把当前状态描述为全部完成，不能更新真实 Windows E2E 条目为 PASS，也不能声称正式 Release 已创建。

## 1. 状态口径

| 状态 | 含义 |
| --- | --- |
| `IMPLEMENTED_AUTOMATED_VERIFIED` | 源码存在，直接自动化测试已执行通过；结论范围不超过测试覆盖。 |
| `IMPLEMENTED_NOT_RUNTIME_VERIFIED` | 实现和契约测试存在，但要求的真实平台、外部服务或完整流水线没有运行证据。 |
| `EXTERNAL_NOT_RUN` | 依赖专用外部环境，当前没有可核验的实际运行记录。 |

## 2. 本次直接执行证据

### Windows 安装器自动化

执行范围：`packaging/windows/tests/*.Tests.ps1`，逐文件使用 Windows PowerShell 运行。

结果：16/16 测试组，108/108 用例通过，0 失败。

覆盖：发行签名与固定信任锚、文件和路径摘要、六镜像身份及平台、预检退出码、状态机与原子落盘、配置和 ACL、随机密钥、证书与 CurrentUser Root 操作边界、十服务与唯一端口、日志脱敏、doctor、完整备份、恢复保护、支持包、默认卸载/Purge、离线更新、资产切换和回滚契约，以及正向/负向 Windows E2E 编排契约。

证据强度：`IMPLEMENTED_AUTOMATED_VERIFIED`。其中名称包含 E2E 的 PowerShell 测试验证的是编排源代码与公共模块行为，不是十服务真实安装。

### Phase 6 发布专项

执行范围：

- `backend/tests/test_release_workflows.py`
- `backend/tests/test_windows_release_builder.py`
- `backend/tests/test_windows_release_verifier.py`
- `backend/tests/test_windows_online_release.py`
- `backend/tests/test_third_party_license_report.py`
- `backend/tests/test_release_asset_finalizer.py`
- `backend/tests/test_release_vulnerability_gate.py`
- `backend/tests/test_release_upgrade_contract.py`
- `backend/tests/test_release_compose.py`
- `backend/tests/test_release_nginx_image.py`

结果：47/47 用例通过，0 失败。

覆盖：严格 Tag 契约、六镜像候选矩阵、SBOM/Grype/Cosign/来源证明接线、许可证生成、OCI 六镜像归档、RSA 离线包构建、在线引导包构建、两类独立包验证、SBOM ZIP、顶层摘要签名、漏洞豁免、发行 Compose、自包含 Nginx 镜像、单机三轮快照复原门禁和发布 Runbook。

补充 Windows 烟测：生成的 `install-online.ps1` 通过 PowerShell AST 语法解析；将发行地址指向不可达的 `https://127.0.0.1:1/releases` 时以退出码 1 终止，没有进入离线安装器。

证据强度：工作流和发行工具为 `IMPLEMENTED_AUTOMATED_VERIFIED`；GitHub Actions 实际执行为 `IMPLEMENTED_NOT_RUNTIME_VERIFIED`。

### 全仓后端回归

执行范围：隔离 PostgreSQL 16 与 Redis 环境，使用仓库规定的 `DATABASE_URL_TEST`，执行 `python -m pytest -m "not integration" --tb=short -q`。

结果：1979 条用例完成收集；1944 条选中，其中 1939 条通过、5 条跳过；35 条 integration 用例按既定基线排除，0 失败，8 条弃用或命名空间警告。

证据强度：`IMPLEMENTED_AUTOMATED_VERIFIED`。该结果不包含显式标记为 integration 的 35 条用例，也不替代 Windows 十服务运行验收。

### 前端构建与浏览器回归

执行范围：前端依赖安全契约、Next.js 生产构建与 TypeScript 检查，以及连接隔离测试后端的完整 Chromium 套件。最终浏览器命令显式使用 `--retries=0`，避免以重试掩盖波动。

结果：安全契约 1/1 通过；生产构建完成 22 个静态页面生成并通过类型检查；Playwright 108/108 通过，0 失败、0 flaky。

首轮浏览器执行实际暴露 6 条失败与 3 条 flaky。失败用例先作为复现证据保留，随后修复默认口令过时断言、任务详情非精确选择器、页面稳定等待和配置向导漏模拟通知接口导致的会话过期竞态；对应提交为 `3400796` 与 `ad4db7d`。

证据强度：`IMPLEMENTED_AUTOMATED_VERIFIED`。本机浏览器结果不替代 Tag 工作流在 GitHub 托管环境中的实际执行记录。

### GitHub main CI

运行：`33171589490`，commit `3fb1b2ffcdc9f907cb96c30d67b7ebc952fa6462`。

结果：前端依赖安全契约与生产构建成功；后端使用 Python 3.11、PostgreSQL 16/pgvector 与 Redis 完成 `pip-audit` 和全量测试，1982 条通过、5 条跳过、0 失败、8 条警告，应用覆盖率 81%。

前两次远端运行作为失败复现证据保留：`33170171376` 被 `pypdf 6.14.2` 的两项已知漏洞阻断，升级到 6.16.2 后 `pip-audit` 通过；`33170591361` 随后暴露测试环境缺少 `jsonschema`，通过新增精确固定的 `backend/requirements-test.txt` 并让 CI/Tag 工作流共同安装后修复。

证据强度：main 分支 CI 为 `IMPLEMENTED_AUTOMATED_VERIFIED`；它不替代 Tag 发行、供应链签名和三轮 Windows 断网 E2E。

## 3. Phase 3

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| Windows 10/11 x64、PowerShell 5.1、Docker Desktop Linux Containers、Compose v2、CPU/内存/磁盘/端口/目录预检 | `Installer.Preflight.Tests.ps1` 的 17 个事实与退出码用例 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 固定信任锚、RSA manifest 签名、文件摘要、危险路径、版本及升级字段 | Artifact、UpdatePackage、Python builder/verifier 测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 恰好六镜像、`linux/amd64`、RepoDigest/Image ID、离线加载与已加载复核 | `Installer.Images.Tests.ps1`、builder/verifier 测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 随机系统密钥、管理员密码、特殊字符 URL、`system.env` 不覆盖和 ACL | `Installer.Config.Tests.ps1` | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 本地独立 CA、叶子证书、用户同意 CurrentUser Root、精确 Thumbprint 删除 | `Installer.Certificate.Tests.ps1` | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 10 个服务、仅 `127.0.0.1:10443`、健康等待与 Bootstrap Ready | Services、Compose、Backend preflight 测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 干净 Windows 11 完全断网十服务安装 | 编排脚本已实现，无真实运行 JSON/日志 | `EXTERNAL_NOT_RUN` |

## 4. Phase 4

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| `start/stop/restart/status/doctor` 命令与只读边界 | Controller、Doctor、Logging 测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 完整备份、最终校验、ACL、失败退出码 60 | `Installer.Backup.Tests.ps1` | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 恢复路径边界、二次确认、保护备份、失败保持入口停止及退出码 61 | `Installer.Restore.Tests.ps1` | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 默认卸载保留数据、Purge 双确认、归属证明、重解析点和越界保护 | `Installer.Uninstall.Tests.ps1` | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 脱敏支持包、白名单文件和二次秘密扫描 | SupportBundle、Logging 测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 真实数据备份、恢复、卸载和 Purge | 正向 E2E 已编排，无十服务实际记录 | `EXTERNAL_NOT_RUN` |

## 5. Phase 5

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| 只接受同一信任链、本地签名 ZIP、严格递增且显式支持的版本 | UpdatePackage、UpgradeContract 测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 安全解压、目录穿越拒绝、资产原子切换、旧资产快照 | UpdatePackage 测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 停入口前健康检查、完整保护备份、迁移、新版健康、管理员登录和核心 API 冒烟 | Controller 顺序契约与实现源代码 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 失败恢复数据库、旧 manifest/Compose/镜像并区分 71/72 | Controller 与回滚契约测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 两个正式版本的真实升级、失败注入和旧版恢复健康 | 尚无两套正式离线候选及 Windows 运行证据 | `EXTERNAL_NOT_RUN` |

## 6. Phase 6

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| 自包含 Nginx、离线 ZIP、RSA manifest、固定信任锚、独立 verifier | 发行专项测试和本机完全离线 Nginx 构建记录 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 六镜像 SPDX SBOM、第三方许可证、Grype Critical/High 门禁与时效豁免 | 生成器、评估器与工作流契约测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 六镜像 Cosign 签名、SBOM 证明、GitHub 来源证明 | Tag 工作流已接线，尚未在 GitHub 执行 | `IMPLEMENTED_NOT_RUNTIME_VERIFIED` |
| 离线 ZIP、SBOM ZIP、许可证、`SHA256SUMS` 和 RSA 顶层签名 | 生成器及独立验签测试；候选工作流已接线 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 后端回归、前端安全契约、生产构建和 Chromium E2E | 本机隔离回归通过；GitHub main CI 1982 passed/5 skipped、覆盖率 81%，前端构建成功；本机 Chromium 108/108 且禁用重试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 端口占用、Docker 停止、Windows Containers、低磁盘、manifest/镜像损坏 | 负向 E2E 编排、精确退出码与无副作用检查已实现 | `IMPLEMENTED_NOT_RUNTIME_VERIFIED` |
| `IMAGES_LOADED`、`CONFIG_CREATED`、`SERVICES_STARTING` 中断重试及重复安装不重置密钥 | 中断进程、重试、认证冒烟和身份摘要编排已实现 | `IMPLEMENTED_NOT_RUNTIME_VERIFIED` |
| 三轮独立纯净 Windows 11 完全断网 E2E | 一台 Windows 11 经三次黄金快照复原、round 标签、ephemeral 注册、消费标记和代次汇总的门禁已实现；runner 与证据不存在 | `EXTERNAL_NOT_RUN` |
| 在线发行包 | `Kanyikan-vX.Y.Z-windows-amd64-online.zip` 构建器、独立验证器及固定信任锚/下载/顶层 RSA/离线 ZIP SHA256/同安装器契约已实现并测试 | `IMPLEMENTED_AUTOMATED_VERIFIED` |
| 正式 GitHub Release | 三轮证据通过后创建草稿、上传在线/离线/SBOM/许可证/摘要/签名并公开的作业已实现，尚未在 GitHub 执行 | `IMPLEMENTED_NOT_RUNTIME_VERIFIED` |

## 7. 已覆盖的关键边缘情况

- manifest、签名、静态文件、镜像 tar、RepoDigest、Image ID 和平台任一漂移即拒绝。
- ZIP 目录穿越、多个顶层目录、绝对路径、反斜杠和越界删除被阻断。
- 端口占用时不结束占用者；预检失败不生成状态、配置、证书、日志或 Compose 资源。
- 安装中断后只从最后成功状态继续；重复安装比较 `system.env`、叶子私钥和根证书摘要，禁止重置身份。
- Purge 在删除日志目录前关闭日志句柄，且在任何破坏动作前完成全部路径和归属复核。
- 控制器密码只经子进程环境传递；输出落盘前阻断密码、Token、JWT、Cookie、认证 URL 和 PEM 私钥，JSON 只记录相对路径与 SHA256。
- 更新失败区分“旧版恢复健康”与“回滚不完整/入口保持停止”，分别退出 71 与 72。

## 8. 解除 NO-GO 的必要顺序

1. 为仓库配置配对的 RSA Secrets，并准备一台带黄金快照、断网/负向 Hook 的 Windows 11 虚拟机。
2. 恢复黄金快照，以 round 1 标签和新的 generation UUID 注册 ephemeral runner；第一轮完成后关机。
3. 对 round 2、round 3 分别重新恢复同一黄金快照、生成新 UUID 并重新注册，禁止直接复用上一轮系统状态。
4. 推送候选 Tag，保存六镜像签名/证明、`release-candidate`、三轮 Windows JSON、快照轮转 JSON 和脱敏输出；确认草稿 Release 仅在全部资产上传后公开。
5. 使用两套正式版本执行更新成功、迁移/健康失败回滚成功和回滚失败三类真实 Windows 场景。
6. 本机全仓后端非集成、前端构建和 108 条浏览器回归已完成；候选 Tag 仍须在 GitHub 工作流中重跑相同门禁并保存日志。根据实际外部证据更新 `ACCEPTANCE_MATRIX.md`，未执行的真实 Windows E2E 条目不得写 PASS。
