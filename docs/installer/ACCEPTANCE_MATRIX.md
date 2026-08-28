# Kanyikan Windows 离线安装器验收矩阵

状态：Phase 0 契约基线，全部实现项初始为 `NOT_IMPLEMENTED`

适用契约：`docs/installer/INSTALLER_SPEC.md` 契约版本 1

目标版本：1.0.0

## 1. 记录规则

状态只允许：

- `NOT_IMPLEMENTED`：契约已定义，尚无实现证据。
- `PASS`：在指定环境按步骤执行并保存了可复核证据。
- `FAIL`：已执行但不符合预期。
- `BLOCKED`：外部环境阻塞，必须记录负责人和解除条件。
- `N/A`：仅在契约明确不适用时使用，并记录批准人和原因。

证据必须记录测试时间、Git commit、发行版本、Windows/Docker/Compose 版本、命令或人工步骤、退出码和脱敏输出位置。单元或模拟测试不能替代标记为“真实 Windows E2E”的条目。

优先级含义：`P0` 为发布硬门禁，`P1` 为候选发布门禁，`P2` 为运维质量项。

## 2. 平台与预检

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| ENV-001 | P0 | Windows E2E | Windows 11 x64 中文环境、Docker Desktop Linux Containers | 双击 `install.cmd` | 预检通过并进入制品验证 | NOT_IMPLEMENTED |
| ENV-002 | P0 | 自动化 | 非 Windows 或非 x64 | 运行 `install` | 退出 `20`，无持久副作用 | NOT_IMPLEMENTED |
| ENV-003 | P0 | 自动化 | PowerShell 低于 5.1 | 运行 `install` | 退出 `20`，提示最低版本 | NOT_IMPLEMENTED |
| ENV-004 | P0 | Windows E2E | 未安装 Docker Desktop | 双击安装 | 退出 `21`，不尝试联网安装 | NOT_IMPLEMENTED |
| ENV-005 | P0 | Windows E2E | Docker Desktop 已安装但 Engine 未启动 | 双击安装 | 退出 `21`，给出启动指引 | NOT_IMPLEMENTED |
| ENV-006 | P0 | Windows E2E | Docker 使用 Windows Containers | 双击安装 | 退出 `21`，提示切换 Linux Containers | NOT_IMPLEMENTED |
| ENV-007 | P0 | 自动化 | Compose v2 不可用 | 运行 `install` | 退出 `21`，不调用旧版 `docker-compose` | NOT_IMPLEMENTED |
| ENV-008 | P0 | 自动化 | CPU 少于 4 核 | 运行 `install` | 退出 `22`，无镜像加载 | NOT_IMPLEMENTED |
| ENV-009 | P0 | 自动化 | 内存少于 8 GiB | 运行 `install` | 退出 `22`，无镜像加载 | NOT_IMPLEMENTED |
| ENV-010 | P0 | Windows E2E | 安装卷可用空间少于 20 GiB | 双击安装 | 退出 `22`，无镜像加载 | NOT_IMPLEMENTED |
| ENV-011 | P0 | Windows E2E | 10443 被其他进程占用 | 双击安装 | 退出 `22`，报告占用且不结束进程 | NOT_IMPLEMENTED |
| ENV-012 | P0 | Windows E2E | 安装路径含中文和空格 | 完整安装、启停、备份 | 全流程成功且路径未被截断 | NOT_IMPLEMENTED |
| ENV-013 | P1 | 自动化 | 安装目录不可写 | 运行 `install` | 退出 `22`，不留下半成品 | NOT_IMPLEMENTED |
| ENV-014 | P1 | 自动化 | Docker 配置了含凭据代理 | 运行 `doctor` | 仅报告代理启用状态，不输出地址凭据 | NOT_IMPLEMENTED |

## 3. 制品、签名与镜像

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| ART-001 | P0 | Schema | 合法 `release-manifest.json` | 以正式 Schema 校验 | 校验通过 | NOT_IMPLEMENTED |
| ART-002 | P0 | Schema | 缺字段、额外字段或非法路径的 manifest | 校验 | 拒绝并定位字段 | NOT_IMPLEMENTED |
| ART-003 | P0 | 自动化 | manifest 被修改 | 运行 `install` | 签名失败，退出 `30`，无镜像加载 | NOT_IMPLEMENTED |
| ART-004 | P0 | 自动化 | `release-manifest.sig` 被替换 | 运行 `install` | 签名失败，退出 `30` | NOT_IMPLEMENTED |
| ART-005 | P0 | 自动化 | 任一静态文件内容被修改 | 运行 `install` | SHA256 失败，退出 `30` | NOT_IMPLEMENTED |
| ART-006 | P0 | 自动化 | 文件大小或清单路径不一致 | 运行 `install` | 校验失败，禁止读取为命令 | NOT_IMPLEMENTED |
| ART-007 | P0 | 自动化 | manifest 路径含 `..`、绝对路径或反斜杠 | 校验/安装 | Schema 或路径规范化拒绝 | NOT_IMPLEMENTED |
| ART-008 | P0 | 自动化 | `VERSION` 与 manifest 不一致 | 运行 `install` | 退出 `30` | NOT_IMPLEMENTED |
| ART-009 | P0 | 自动化 | 目标 OS/架构不是 Windows/amd64 | 运行 `install` | 退出 `30` | NOT_IMPLEMENTED |
| ART-010 | P0 | 集成 | 标准离线镜像 tar | `docker load` 后核对 | 恰好声明的六个镜像存在，ID/digest 一致 | NOT_IMPLEMENTED |
| ART-011 | P0 | 集成 | tar 缺一个镜像或多一个未声明镜像 | 安装 | 退出 `31`，不启动服务 | NOT_IMPLEMENTED |
| ART-012 | P0 | 集成 | 镜像为非 `linux/amd64` | 安装 | 退出 `30` 或 `31`，不启动服务 | NOT_IMPLEMENTED |
| ART-013 | P0 | 静态 | Compose 与 manifest | 扫描镜像引用 | 全部 `@sha256:` 固定，无 `latest`，`pull_policy: never` | NOT_IMPLEMENTED |
| ART-014 | P0 | 安全 | 完整发行包 | 秘密与私钥扫描 | 无 API Key、密码、Cookie、JWT、客户数据或私钥 | NOT_IMPLEMENTED |
| ART-015 | P1 | 自动化 | 离线且 Docker 缓存为空 | 完整安装 | 不访问 Registry 或下载源，安装成功 | NOT_IMPLEMENTED |
| ART-016 | P0 | 自动化 | 换入另一把公钥并重签 manifest | 安装 | 公钥指纹与固定信任锚不符，退出 `30` | NOT_IMPLEMENTED |

## 4. 配置、密钥与 TLS

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SEC-001 | P0 | 单元 | 多次生成系统密钥 | 统计长度、格式和重复 | 满足熵要求，无重复，Fernet key 可用 | NOT_IMPLEMENTED |
| SEC-002 | P0 | 单元 | 密码含空格、`@`、`:`、`#`、`%` | 生成并加载 env/URL | 值可逆，URL 凭据正确编码 | NOT_IMPLEMENTED |
| SEC-003 | P0 | Windows E2E | 两次管理员密码不一致 | 安装 | 在持久副作用前拒绝，不回显密码 | NOT_IMPLEMENTED |
| SEC-004 | P0 | 自动化 | 管理员密码不符合 Backend 策略 | 安装 | 明确拒绝，不生成配置 | NOT_IMPLEMENTED |
| SEC-005 | P0 | Windows E2E | 已生成 `system.env` | 检查 ACL | 仅当前用户与 Administrators 可读 | NOT_IMPLEMENTED |
| SEC-006 | P0 | 自动化 | 安装、失败、doctor、支持包 | 扫描全部输出 | 所有敏感值为 `[REDACTED]`，无可反推摘要 | NOT_IMPLEMENTED |
| TLS-001 | P0 | 单元 | 全新证书生成 | 解析叶子证书 | SAN 含 `localhost` 和 `127.0.0.1`，用途正确 | NOT_IMPLEMENTED |
| TLS-002 | P0 | 单元 | 连续两次全新安装 | 比较 CA/叶子密钥 | 不复用 CA 或私钥 | NOT_IMPLEMENTED |
| TLS-003 | P0 | 集成 | 签发成功 | 检查文件与容器挂载 | CA 私钥已删除且未进入 Nginx | NOT_IMPLEMENTED |
| TLS-004 | P0 | Windows E2E | 用户同意信任 CA | 安装并打开入口 | 当前用户 Root 中存在记录 Thumbprint，浏览器无警告 | NOT_IMPLEMENTED |
| TLS-005 | P1 | Windows E2E | 用户拒绝信任 CA | 安装 | 服务仍启动，明确提示浏览器警告 | NOT_IMPLEMENTED |
| TLS-006 | P0 | 自动化 | 证书过期、SAN 错误或私钥不匹配 | 启动/健康检查 | 不进入 `HEALTHY`，退出 `41` 或 `51` | NOT_IMPLEMENTED |
| TLS-007 | P0 | Windows E2E | 默认卸载 | 检查证书存储 | 仅按记录 Thumbprint 删除本产品 CA | NOT_IMPLEMENTED |
| TLS-008 | P1 | 单元 | manifest 中最小/最大合法有效期及越界值 | 生成或校验证书配置 | 1～825 天叶子证书、825～3650 天 CA 可用；越界值拒绝 | NOT_IMPLEMENTED |

## 5. Compose、启动与首次配置

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| RUN-001 | P0 | 静态 | 发行版 Compose | `docker compose config` 与规则扫描 | 不依赖源码目录，固定网络/卷，配置可渲染 | NOT_IMPLEMENTED |
| RUN-002 | P0 | 静态 | 发行版 Compose | 检查端口 | 仅 `127.0.0.1:10443`，其他服务无宿主端口 | NOT_IMPLEMENTED |
| RUN-003 | P0 | 集成 | 六个镜像已加载 | 启动 Compose | 10 个服务依赖顺序正确并全部健康 | NOT_IMPLEMENTED |
| RUN-004 | P0 | 集成 | Registry 不可访问 | 启动/重启 | 不拉取镜像且成功 | NOT_IMPLEMENTED |
| RUN-005 | P0 | 后端 | 空数据库、无 LLM/搜索/Sentry | 启动 Backend | Bootstrap Ready，可登录和访问设置向导 | NOT_IMPLEMENTED |
| RUN-006 | P0 | 后端/E2E | 无已测试 Provider | 尝试创建研究任务 | 被 Execution Ready 阻止并引导配置 | NOT_IMPLEMENTED |
| RUN-007 | P0 | E2E | LLM 与搜索测试成功，路由/抓取/预算完整 | 完成向导 | 状态为 READY，跳转新建任务 | NOT_IMPLEMENTED |
| RUN-008 | P0 | E2E | 已完成首次配置 | 刷新、重启服务、重新登录 | 配置加密持久保存，READY 状态不丢失 | NOT_IMPLEMENTED |
| RUN-009 | P0 | 安全 | `local_appliance` | 检查监听地址、TLS、Cookie、Origin、SSRF | 仅 loopback、HTTPS、Secure Cookie、SSRF 门禁有效 | NOT_IMPLEMENTED |
| RUN-010 | P0 | 后端 | 缺系统密钥/TLS/安全 Cookie/SSRF 配置 | 启动 Backend | Bootstrap preflight 阻止启动并准确报错 | NOT_IMPLEMENTED |
| RUN-011 | P1 | Windows E2E | 服务健康但浏览器启动失败 | 安装完成 | 返回 `0`，终端显示唯一入口 | NOT_IMPLEMENTED |
| RUN-012 | P0 | Windows E2E | Windows 重启 | Docker Desktop 启动后访问入口 | 服务自动恢复，数据仍存在 | NOT_IMPLEMENTED |

## 6. 状态机、重试和管理命令

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| STM-001 | P0 | 单元 | 每个正常阶段 | 写入下一状态 | 只允许契约定义的顺序，原子落盘 | NOT_IMPLEMENTED |
| STM-002 | P0 | 单元 | 任一步骤抛错 | 读取状态 | 保留最后成功状态并写脱敏 `lastFailure` | NOT_IMPLEMENTED |
| STM-003 | P0 | Windows E2E | 在镜像加载、配置创建、服务启动阶段分别中断 | 再次运行安装 | 复核已完成产物，从首个未完成阶段继续 | NOT_IMPLEMENTED |
| STM-004 | P0 | 自动化 | 状态文件损坏或安装根不一致 | 执行写命令 | 退出 `90`，不猜测资源、不删除数据 | NOT_IMPLEMENTED |
| STM-005 | P1 | Windows E2E | 已安装 | 再次双击安装 | 只做一致性检查，不重置密钥/数据 | NOT_IMPLEMENTED |
| CMD-001 | P1 | 集成 | 服务已启动/已停止 | 重复 `start`、`stop`、`restart` | 幂等且数据保留 | NOT_IMPLEMENTED |
| CMD-002 | P1 | 自动化 | 正常与部分故障状态 | 执行 `status` | 只读输出版本、状态、健康和入口 | NOT_IMPLEMENTED |
| CMD-003 | P1 | 自动化 | Docker、端口、镜像、容器、端点、外网多种状态 | 执行 `doctor` | 逐项诊断；Provider 未配置不误报安装失败 | NOT_IMPLEMENTED |
| CMD-004 | P0 | 自动化 | 所有命令失败分支 | 检查退出码与终端输出 | 符合规格表且包含可操作下一步 | NOT_IMPLEMENTED |

## 7. 备份与恢复

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| BAK-001 | P1 | 集成 | 有数据库、Skill、快照数据 | 执行 `backup` | 生成不覆盖的完整备份和校验清单 | NOT_IMPLEMENTED |
| BAK-002 | P0 | 自动化 | 数据库导出不完整或摘要失败 | 执行 `backup`/`update` | 备份标记无效；更新不进入停服/迁移 | NOT_IMPLEMENTED |
| BAK-003 | P1 | 集成 | 合法完整备份 | 二次确认后 `restore` | 数据一致、服务健康、版本元数据匹配 | NOT_IMPLEMENTED |
| BAK-004 | P0 | 安全 | 路径穿越、绝对外部路径、重解析点或含脚本备份 | 执行 `restore` | 拒绝，不读取/执行越界内容 | NOT_IMPLEMENTED |
| BAK-005 | P0 | 集成 | 恢复中途失败 | 检查系统 | 入口停止，恢复前保护备份保留，不以空库启动 | NOT_IMPLEMENTED |
| BAK-006 | P1 | 人工 | 只保存数据备份但丢失 `system.env` | 演练恢复 | 明确证明加密配置不可恢复，文档已有预警 | NOT_IMPLEMENTED |

## 8. 更新与回滚

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| UPD-001 | P0 | 集成 | 合法递增版本更新包 | 执行 `update -Package` | 完整备份、迁移、健康、冒烟后记录新版本 | NOT_IMPLEMENTED |
| UPD-002 | P0 | 自动化 | 签名无效、同版本或降级包 | 执行更新 | 退出 `70`，停服前终止 | NOT_IMPLEMENTED |
| UPD-003 | P0 | 自动化 | 迁移不可逆且无有效备份 | 执行更新 | 停止更新，不修改数据 | NOT_IMPLEMENTED |
| UPD-004 | P0 | 集成 | 新版本迁移或健康检查失败 | 执行更新 | 恢复数据库、旧 manifest/Compose/镜像并验证旧版健康，退出 `71` | NOT_IMPLEMENTED |
| UPD-005 | P0 | 故障注入 | 回滚过程也失败 | 执行更新 | 入口保持停止，退出 `72`，生成脱敏诊断 | NOT_IMPLEMENTED |
| UPD-006 | P0 | Windows E2E | 连续两个正式版本 | 两轮升级和回滚 | 数据、密钥、证书和用户登录均保持 | NOT_IMPLEMENTED |
| UPD-007 | P0 | 安全 | 更新包携带不同发行公钥 | 执行更新 | 使用已安装公钥指纹拒绝更新，停服前终止 | NOT_IMPLEMENTED |

## 9. 卸载与资源边界

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| UNS-001 | P1 | Windows E2E | 正常安装且有业务数据 | 默认 `uninstall` | 容器/网络/导入 CA 删除，数据卷、配置、备份保留 | NOT_IMPLEMENTED |
| UNS-002 | P0 | 安全 | 同机存在名称相似的其他 Compose 项目和卷 | 默认卸载/Purge | 其他项目资源不受影响 | NOT_IMPLEMENTED |
| UNS-003 | P0 | 自动化 | Purge 未输入固定确认文本 | 执行 `uninstall -PurgeData` | 不删除任何持久数据 | NOT_IMPLEMENTED |
| UNS-004 | P0 | Windows E2E | 有有效备份 | 确认 Purge | 精确删除本项目卷、配置和数据目录 | NOT_IMPLEMENTED |
| UNS-005 | P0 | Windows E2E | 无有效备份 | 确认 Purge | 显示第二次不同告警并再次确认 | NOT_IMPLEMENTED |
| UNS-006 | P0 | 自动化 | 状态无法证明某资源归属 | 执行卸载 | 保留资源并报告人工步骤 | NOT_IMPLEMENTED |
| UNS-007 | P1 | 自动化 | 已完成卸载 | 重复卸载 | 幂等，不影响其他资源 | NOT_IMPLEMENTED |

## 10. 诊断、安全和业务烟测

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| DIA-001 | P1 | 自动化 | 环境含测试秘密、认证 URL、JWT、PEM 私钥标记 | 生成支持包 | 二次扫描通过，内容均脱敏或排除 | NOT_IMPLEMENTED |
| DIA-002 | P1 | 自动化 | 外部 DNS/API 可用、不可用、未配置 | 运行 `doctor` | 三种状态可区分，不输出请求正文/凭据 | NOT_IMPLEMENTED |
| DIA-003 | P2 | 自动化 | 系统时间明显偏移 | 运行 `doctor` | 报告时间同步风险和修复建议 | NOT_IMPLEMENTED |
| DIA-004 | P1 | 自动化 | 数据卷缺失或镜像 ID 漂移 | 运行 `doctor` | 精确报告差异，不自动删改 | NOT_IMPLEMENTED |
| BUS-001 | P0 | Windows E2E | 首次配置完成 | 新建、暂停、继续任务并回查历史 | 核心业务链成功 | NOT_IMPLEMENTED |
| BUS-002 | P1 | Windows E2E | 有完成报告 | 导出 PDF 和 Word | 文件可打开，内容完整 | NOT_IMPLEMENTED |
| BUS-003 | P1 | Windows E2E | 外网受企业代理/防火墙限制 | 测试 Provider 与网页抓取 | 失败可诊断且不降低 SSRF/TLS 安全设置 | NOT_IMPLEMENTED |

## 11. 发布流水线与发布 Gate

| ID | 优先级 | 类型 | 场景/前置条件 | 操作 | 预期结果 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| REL-001 | P0 | CI | Git tag | 校验 tag | 仅接受 `vX.Y.Z` | NOT_IMPLEMENTED |
| REL-002 | P0 | CI | 后端/前端源码 | 全量测试、构建、E2E | 全部通过 | NOT_IMPLEMENTED |
| REL-003 | P0 | CI | 最终依赖与六镜像 | 依赖审计、镜像扫描 | 无未豁免 Critical/High；豁免字段齐全且未过期 | NOT_IMPLEMENTED |
| REL-004 | P0 | CI | 六个最终镜像 | 生成 SBOM、证明和签名 | 所有镜像均有可验证产物 | NOT_IMPLEMENTED |
| REL-005 | P0 | CI | 发布内容 | 生成在线/离线 ZIP、SHA256、签名、许可证 | 资产命名和摘要正确 | NOT_IMPLEMENTED |
| REL-006 | P0 | Windows E2E | 干净 Windows 11 VM | 完全断网安装 | 唯一入口健康，首次向导可用 | NOT_IMPLEMENTED |
| REL-007 | P0 | 人工+自动 | 候选发布包 | 连续三轮全新安装 | 三轮全部通过，证据独立 | NOT_IMPLEMENTED |
| REL-008 | P0 | CI | 任一 P0 Gate 失败 | 尝试发布 | 不创建正式 GitHub Release | NOT_IMPLEMENTED |
| REL-009 | P1 | 人工 | 安装、运维、故障文档 | 非开发人员盲测 | 无口头补充即可完成安装和恢复 | NOT_IMPLEMENTED |

## 12. 阶段 Gate

### 内部试用版

- [ ] `ART-003`～`ART-015` 的 P0 条目通过。
- [ ] `RUN-002`～`RUN-010` 通过，10 个服务健康。
- [ ] `ENV-001`、`ENV-004`～`ENV-012` 的 P0 条目通过。
- [ ] 首次登录、配置和任务创建流程通过。
- [ ] 完全断网且空 Docker 缓存安装成功。
- [ ] Windows 重启后数据与服务恢复。

### 候选发布版

- [ ] 所有 P0、P1 自动化与 Windows E2E 条目通过。
- [ ] 备份、恢复、升级、回滚、默认卸载和 Purge 全部通过。
- [ ] 本地 CA 安装和精确删除通过，浏览器无证书警告。
- [ ] 诊断包脱敏扫描通过。
- [ ] SBOM、许可证和漏洞门禁通过。

### 正式版

- [ ] CI 自动创建完整 GitHub Release 资产。
- [ ] 连续三轮全新安装通过。
- [ ] 连续两轮跨版本升级与失败回滚通过。
- [ ] 非开发人员完成安装、运维、故障恢复盲测。
- [ ] 所有未关闭 `FAIL`/`BLOCKED` 均有发布负责人书面否决发布；不得以风险接受跳过 P0 安全门禁。
