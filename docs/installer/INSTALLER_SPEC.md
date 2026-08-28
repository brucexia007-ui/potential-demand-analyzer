# Kanyikan Windows 离线安装器规格

状态：Phase 0 冻结候选

契约版本：1

目标产品版本：1.0.0

基线：`main@b02a820`

## 1. 目的与边界

本规格定义 Kanyikan V1 Windows 离线安装包的输入、输出、状态、失败语义和恢复方式。实现、测试、发布流水线和用户文档必须共同遵守本契约；不满足强制条款的制品不得发布。

V1 只支持以下组合：

| 项目 | 唯一支持值 |
| --- | --- |
| 宿主系统 | Windows 10/11 x64 |
| 命令环境 | Windows PowerShell 5.1 或更高版本的 Windows PowerShell |
| 容器运行时 | 已安装且已启动的 Docker Desktop |
| 容器模式 | Linux Containers |
| 容器平台 | `linux/amd64` |
| 部署模式 | `DEPLOYMENT_PROFILE=local_appliance` |
| 部署拓扑 | 单机、单实例 |
| 用户入口 | `https://127.0.0.1:10443` |
| 安装网络 | 可完全断网，不访问 Registry 或其他下载源 |

安装后执行业务任务仍需要访问用户配置的 LLM、搜索 API 和公网网页。

V1 不支持自动安装 Docker Desktop、Windows Containers、ARM64、Linux/macOS 安装器、多节点、高可用、公网或局域网部署、Kubernetes，以及历史非正式安装方式迁移。实现不得为这些场景添加兼容分支。

## 2. 产品不变量

1. 安装、启动、升级和恢复始终保持 `ENV=production` 与 `DEPLOYMENT_PROFILE=local_appliance`。
2. 宿主机只允许 `127.0.0.1:10443` 一个已发布端口；数据库、Redis、Backend、Frontend 和 Browserless 不得直接发布端口。
3. 浏览器入口强制 HTTPS，认证 Cookie 强制 Secure，本地 Origin 仅允许产品入口所需值。
4. 离线包包含 Backend、Frontend、PostgreSQL/pgvector、Redis、Nginx、Browserless 六个唯一的 `linux/amd64` 镜像；10 个 Compose 服务复用这些镜像。
5. 所有镜像引用必须固定到 `sha256` digest，Compose 必须设置 `pull_policy: never`，不得使用 `latest` 或仅标签引用。
6. 系统启动就绪（Bootstrap Ready）不依赖 LLM、搜索或 Sentry；任务执行就绪（Execution Ready）必须由配置中心单独判定。
7. 普通停止、重启、修复、升级失败和默认卸载不得删除数据卷、配置、备份或加密密钥。
8. `CONFIG_ENCRYPTION_KEY` 在安装后必须永久保留，升级和恢复不得重新生成。
9. 安装器、日志、状态文件、诊断包和发布制品不得泄露密码、API Key、Cookie、JWT、DSN 凭据或任何私钥。
10. 所有破坏性操作必须限定到 release manifest 和安装状态明确记录的本项目资源。

## 3. 硬件与环境要求

安装前必须验证：

| 检查项 | 通过标准 | 失败处理 |
| --- | --- | --- |
| 操作系统 | Windows 10/11 x64 | 终止，不修改系统 |
| PowerShell | Windows PowerShell 5.1+ | 终止，不尝试安装或切换 Shell |
| Docker Desktop | 已安装，Engine 可响应 | 终止并给出启动指引 |
| 容器模式 | Engine OSType 为 `linux` | 终止并提示切换 Linux Containers |
| Compose | `docker compose` v2 可用 | 终止 |
| CPU | 至少 4 个逻辑处理器 | 终止 |
| 内存 | 至少 8 GiB | 终止 |
| 可用磁盘 | 安装所在卷至少 20 GiB | 终止 |
| 入口端口 | `127.0.0.1:10443` 未被其他进程占用 | 终止并报告占用，不结束进程 |
| 安装目录 | 当前用户可读写，规范化后为发行包根目录 | 终止 |
| 路径 | 支持中文和空格 | 任意路径拼接失败均视为安装器缺陷 |
| 镜像平台 | manifest 中全部为 `linux/amd64` | 终止 |

环境预检不得拉取镜像、安装软件、关闭进程、修改防火墙或改写 Docker Desktop 配置。Docker 代理状态只记录是否启用，不记录代理凭据。

## 4. 发行包输入

发行包根目录必须包含：

```text
Kanyikan-v1.0.0-windows-amd64/
├── install.cmd
├── kanyikan.ps1
├── lib/
│   └── Kanyikan.Installer.psm1
├── compose.release.yml
├── release-manifest.json
├── release-manifest.sig
├── manifest.sha256
├── images/
│   └── kanyikan-images-windows-amd64.tar
├── config/
│   └── system.env.template
├── docs/
│   ├── 快速安装说明.md
│   ├── 故障排查.md
│   └── 第三方许可证.html
├── public-key.pem
├── VERSION
└── LICENSE
```

输入契约：

- `release-manifest.json` 必须通过 `packaging/release-manifest.schema.json` 校验，并声明受约束的 CA/叶子证书有效期。
- `release-manifest.sig` 是对 `release-manifest.json` 原始字节的 RSA-SHA256 签名。
- `public-key.pem` 仅包含用于发行验证的公钥，其 SHA256 必须同时匹配 manifest 与安装控制器固定的信任指纹。
- 首次安装的外层信任边界是官方发布渠道公布并签名的 ZIP 摘要；manifest 签名负责包内逐项验证。更新时必须使用安装状态中保存的原发行公钥指纹验证，V1 不接受更新包自行替换信任公钥。
- `manifest.sha256` 覆盖 manifest 声明的所有静态文件，并使用相对于包根目录的规范化 `/` 路径。
- 离线镜像 tar 必须包含 manifest 声明的全部六个镜像，且不得包含未声明镜像。
- `VERSION` 必须与 manifest 的 `release.version` 完全一致。
- 发行包不得预置 `system.env`、证书私钥、安装状态、备份或客户数据。

验证顺序固定为：包结构与 Schema → manifest 签名 → 文件 SHA256/大小 → 版本与目标平台 → `docker load` → 已加载镜像 ID/digest/平台。签名或摘要失败必须立即终止；未经验证的数据不得作为命令或路径执行。

## 5. 安装输出与资源归属

用户从解压后的发行包根目录运行安装器。V1 不迁移或复制安装目录；该目录即安装根目录。安装后新增：

```text
config/
├── system.env
└── certs/
    ├── localhost.crt
    ├── localhost.key
    └── local-root-ca.crt

state/
└── install-state.json

data/
├── backups/
├── exports/
└── support-bundles/

logs/
└── kanyikan-YYYYMMDD-HHmmss.log
```

Docker 资源使用固定 Compose project name `kanyikan`。持久数据分为：

| 数据 | 位置 | 默认卸载 | Purge 卸载 |
| --- | --- | --- | --- |
| PostgreSQL | 项目专属 named volume | 保留 | 删除 |
| Redis | 项目专属 named volume | 保留 | 删除 |
| Skill 与快照 | 项目专属 named volume | 保留 | 删除 |
| `system.env` 与证书 | 安装根目录 | 保留 | 删除 |
| 安装状态 | 安装根目录 | 保留 | 删除 |
| 备份、导出、诊断包 | 安装根目录 | 保留 | 删除前再次告警 |
| 本地根证书信任 | `Cert:\CurrentUser\Root` | 精确删除 | 精确删除 |

资源名称和本地 CA Thumbprint 必须记录在安装状态中。不得使用模糊前缀、通配符或目录猜测发现卸载目标。

## 6. 命令契约

唯一管理入口为：

```powershell
.\kanyikan.ps1 <command>
```

`install.cmd` 只负责在自身目录调用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\kanyikan.ps1 install`，并保留退出码与窗口中的可读结果。V1 不接受管理员密码、系统密钥或 Provider 凭据的命令行参数。

| 命令 | 前置条件 | 成功输出 | 可重入/恢复语义 |
| --- | --- | --- | --- |
| `install` | 合法发行包；满足环境要求 | 服务健康、状态为 `INSTALLED`，打开唯一入口 | 依据状态文件从最后一个已验证状态继续；已安装时只做一致性检查 |
| `start` | 已创建配置和证书 | 启动服务并等待 Bootstrap Ready | 重复执行无副作用 |
| `stop` | 已有安装状态 | 停止容器，保留全部数据 | 已停止视为成功 |
| `restart` | 已安装 | 有序停止、启动并等待健康 | 失败保留数据并记录失败阶段 |
| `status` | 无 | 输出版本、状态、容器健康和入口可用性 | 只读；敏感值不得输出 |
| `doctor` | 无 | 输出环境、资源、镜像、容器、端点和外部连通性诊断 | 只读；无 Provider 时标记“未配置”而非安装失败 |
| `backup` | 数据卷存在 | 在 `data/backups/` 生成完整、校验通过的备份 | 不覆盖既有备份 |
| `restore -Backup <path>` | 合法备份目录内的已验证备份；交互确认 | 数据、Skill/快照和所需配置恢复并通过健康检查 | 失败停止服务并保留恢复前保护备份 |
| `update -Package <path>` | 已安装；签名有效且版本递增；迁移可回滚 | 新版本健康且冒烟测试通过 | 失败自动恢复数据库、旧 manifest 和旧镜像 |
| `uninstall` | 已安装或有明确状态记录 | 删除容器、网络和本地信任证书，保留数据与配置 | 重复执行只处理状态中仍存在的资源 |
| `uninstall -PurgeData` | 输入指定确认文本；已显示最近备份时间 | 精确删除本项目资源和本地数据 | 无有效备份时需要第二次不同提示确认 |
| `support-bundle` | 无 | 在 `data/support-bundles/` 生成脱敏诊断包 | 不包含业务正文、凭据或私钥 |

除 `install.cmd` 外不得新增功能重复的顶层脚本。自动打开浏览器失败不影响安装成功，必须同时打印唯一入口。

## 7. 安装状态机

正常迁移只能按下列顺序发生：

```text
NEW
→ PREFLIGHT_OK
→ VERIFIED
→ IMAGES_LOADED
→ CONFIG_CREATED
→ CERT_READY
→ SERVICES_STARTING
→ HEALTHY
→ INSTALLED
```

任一步骤失败时，状态文件保留最后一个成功状态，并另写 `lastFailure`；不得将持久状态推进到未完成阶段。再次运行 `install` 时必须重新验证当前阶段产物，再从第一个未完成阶段继续。

| 状态 | 完成条件 | 重试时必须复核 |
| --- | --- | --- |
| `NEW` | 尚无已验证副作用 | 包根目录与状态文件可写 |
| `PREFLIGHT_OK` | 第 3 节全部检查通过 | 易变的 Docker、资源、磁盘和端口检查 |
| `VERIFIED` | 签名、Schema、文件摘要、版本和目标平台通过 | 静态文件未变，签名仍有效 |
| `IMAGES_LOADED` | 六个镜像已加载且 ID/digest/平台匹配 | 所有声明镜像仍存在且匹配 |
| `CONFIG_CREATED` | 密钥与环境文件原子写入并收紧 ACL | 所有必需键存在、格式有效、ACL 合格 |
| `CERT_READY` | 叶子证书有效且匹配私钥，CA 私钥已删除 | SAN、有效期、文件 ACL、信任 Thumbprint |
| `SERVICES_STARTING` | Compose 已按固定 project 启动 | 仅操作 manifest 记录的项目资源 |
| `HEALTHY` | 10 个服务健康，`/health` 和 `/ready` 满足 Bootstrap Ready | 容器、端点与唯一端口 |
| `INSTALLED` | 安装版本和资源清单落盘，入口可访问 | manifest、配置、证书、镜像、容器一致性 |

`install-state.json` 至少记录契约版本、产品版本、当前状态、更新时间、安装根目录、manifest SHA256、发行公钥 SHA256、Compose project name、资源精确名称、镜像 ID、CA Thumbprint 和脱敏失败信息。状态文件使用临时文件加同目录原子替换写入；损坏或与安装根目录不一致时不得猜测恢复，应以状态损坏错误终止并提示从备份恢复。

## 8. 配置、密钥与管理员密码

安装器使用密码学安全随机源独立生成：

- `SECRET_KEY`：至少 48 个随机字符或等效 256 bit 熵。
- `CONFIG_ENCRYPTION_KEY`：有效的 Fernet key。
- `POSTGRES_PASSWORD`、`REDIS_PASSWORD`、`BROWSERLESS_TOKEN`：各自至少 256 bit 随机值。

管理员密码必须在安装期间静默交互输入两次并确认相同，不得通过参数、环境继承、剪贴板回显、日志或状态文件传递明文。密码策略由 Backend 的正式认证契约统一执行，安装器必须在产生任何持久副作用前验证其是否满足策略。

`config/system.env` 必须使用可逆且无歧义的环境值编码，支持密码中的空格和 URL 保留字符；连接 URL 中的用户名与密码必须单独进行 URL 编码。文件通过临时文件原子替换生成，并将 ACL 限定为当前用户与本机 Administrators。日志只可记录键名和“已配置/未配置”，不可记录值或可反推值的摘要。

LLM、搜索、模型路由、抓取和预算配置不写入 `system.env`，由首次配置向导写入数据库并加密保存。Sentry 为可选项。

## 9. 本地 TLS 契约

证书由已验证并已加载的 Backend 镜像生成，每次全新安装生成独立本地根 CA 和叶子证书：

- 叶子证书 SAN 必须同时包含 DNS `localhost` 和 IP `127.0.0.1`。
- CA 和叶子证书有效期由已签名 manifest 配置；叶子证书限制为 1～825 天，CA 限制为 825～3650 天，且 CA 到期时间必须晚于叶子证书。
- 叶子证书用途必须允许 TLS Server Authentication，私钥不得加密为交互式启动格式。
- Nginx 只读取叶子证书和叶子私钥，不接触根 CA 私钥。
- 根 CA 私钥在叶子证书签发成功且校验完成后立即删除，不进入备份或诊断包。
- 将根 CA 加入 `Cert:\CurrentUser\Root` 前必须明确询问用户；拒绝信任不阻止服务启动，但必须说明浏览器将显示证书警告。
- 记录实际导入证书的 Thumbprint；卸载只按 Thumbprint 精确删除安装器导入的证书。
- 证书不存在、过期、SAN 不符、私钥不匹配或 ACL 过宽时，服务不得进入 `HEALTHY`。

发行包不得包含固定证书、固定 CA、任何私钥或跨安装共享密钥。

## 10. 就绪模型

### 10.1 Bootstrap Ready

Backend 进程和安装健康检查的硬门禁为：固定镜像摘要、`SECRET_KEY`、`CONFIG_ENCRYPTION_KEY`、管理员密码、PostgreSQL 密码、Redis 密码、Browserless Token、TLS、安全 Cookie、SSRF 防护、数据目录权限以及本地模式仅绑定 loopback。

LLM Provider、搜索 Provider、模型路由和 Sentry 缺失不得阻止登录或进入首次配置向导。

### 10.2 Execution Ready

创建或执行研究任务前必须满足：至少一个已测试通过的 LLM Provider、至少一个已测试通过的搜索 Provider、有效模型路由、抓取配置和预算配置。未就绪时 API 和界面必须阻止任务创建并引导到设置页；不得把它误报为容器或安装失败。

## 11. 健康与超时

安装器必须等待 Compose 声明的 10 个服务全部达到健康状态，并分别验证 HTTPS `/health` 与 `/ready`。健康等待必须有明确总超时、轮询间隔和最近错误摘要；不得无限等待。服务失败时保留容器和日志供诊断，不删除卷，也不自动降级安全设置。

下列结果不影响 Bootstrap Ready：未配置 Provider、Sentry 未配置、浏览器自动打开失败、用户拒绝信任本地 CA。后两项必须给出可操作提示。

## 12. 退出码与错误输出

| 退出码 | 含义 | 可重试性 |
| ---: | --- | --- |
| `0` | 命令成功 | 不适用 |
| `10` | 命令或参数无效 | 修正输入后重试 |
| `20` | 平台或 PowerShell 不支持 | 更换受支持环境 |
| `21` | Docker/Compose 不可用或容器模式错误 | 修复 Docker 后重试 |
| `22` | CPU、内存、磁盘、端口或目录权限不满足 | 修复资源条件后重试 |
| `30` | 包结构、Schema、签名、摘要、版本或平台验证失败 | 换用可信完整发行包 |
| `31` | 镜像加载或镜像身份核对失败 | 修复 Docker 或更换包后重试 |
| `40` | 配置、密钥或 ACL 创建失败 | 修复权限后按状态重试 |
| `41` | 证书生成、校验或信任操作失败 | 修复权限或证书状态后重试 |
| `50` | Compose 启动失败 | 运行 `doctor` 后重试 |
| `51` | 健康检查超时或失败 | 运行 `doctor` 后重试 |
| `60` | 备份失败或备份校验失败 | 不进入升级/恢复破坏阶段 |
| `61` | 恢复失败 | 保留保护备份，人工处理后重试 |
| `70` | 更新包或迁移不允许 | 换用合法递增且可回滚的更新包 |
| `71` | 更新失败但已成功回滚 | 继续使用旧版本并提交诊断包 |
| `72` | 更新失败且回滚未完成 | 停止入口，禁止继续写入并人工恢复 |
| `80` | 卸载或 Purge 失败 | 根据状态文件继续精确清理 |
| `90` | 安装状态损坏或内部未分类错误 | 停止写操作并生成脱敏诊断 |

终端错误必须包含命令、失败阶段、退出码、简短原因、日志路径和下一步操作；不得包含敏感值、完整认证 URL 或原始异常中的凭据。

## 13. 日志与诊断脱敏

日志默认记录时间、级别、命令、阶段、退出码、资源逻辑名和脱敏错误。以下内容在写入前必须按结构化字段和自由文本两层过滤：

- API Key、密码、Cookie、JWT、Bearer Token、Browserless Token。
- `SECRET_KEY`、`CONFIG_ENCRYPTION_KEY`、数据库/Redis 凭据。
- Sentry DSN 或 URL 中的用户信息、密码、查询凭据。
- PEM 私钥、证书签名请求中的私密材料。
- Provider 请求/响应正文和客户业务数据。

脱敏值统一替换为 `[REDACTED]`。诊断包仅允许包含脱敏日志、版本、manifest 公共元数据、镜像 ID/digest、Compose 渲染摘要、容器状态、健康结果和资源容量。生成后必须再次扫描敏感字段和 PEM 私钥标记；扫描失败不得交付诊断包。

## 14. 备份与恢复

完整备份至少包含 PostgreSQL 一致性备份、Skill/快照数据、恢复所需的版本/manifest 元数据和校验清单。`CONFIG_ENCRYPTION_KEY` 等配置密钥不默认复制到普通诊断包；备份命令必须明确提示用户单独保护 `system.env`，否则加密 Provider 配置不可恢复。

每个备份使用不可冲突的时间戳目录，完成后生成摘要并执行可读取校验。未通过校验的备份标记为无效。恢复路径经规范化后必须位于 `data/backups/` 内，拒绝符号链接/重解析点越界、路径穿越、脚本和未声明文件执行。

恢复前必须二次确认并创建恢复前保护备份。恢复失败时停止用户入口，保留失败现场和保护备份，不以空库启动冒充成功。

## 15. 更新与回滚

更新仅接受通过同一发行信任链验证的本地 ZIP。版本必须严格递增，不支持降级安装或跳过声明不允许的迁移路径。

固定顺序为：验证更新包 → 校验版本与迁移 → 停止入口 → 创建并校验完整备份 → 保存旧 manifest/镜像引用 → 加载并核对新镜像 → 数据库迁移 → 启动新版本 → 全部健康 → 登录和核心 API 冒烟 → 记录新版本。

任何迁移若不可逆且没有有效完整备份，更新必须在修改数据前终止。更新失败时依次停止新容器、恢复数据库备份、恢复旧 manifest/Compose、启动旧镜像、验证健康、生成失败诊断包。只有旧版本健康后才返回退出码 `71`；回滚未完成必须返回 `72` 并保持入口停止。

## 16. 卸载

默认 `uninstall` 删除本项目容器、网络和安装器导入的当前用户根证书，保留 named volumes、配置、状态、备份、导出和诊断包。完成后明确显示保留位置。

`uninstall -PurgeData` 必须：

1. 显示将删除的精确资源和最近一次有效备份时间。
2. 要求输入固定确认文本，不能使用 `Y/N` 代替。
3. 没有有效备份时再次显示不同告警并进行第二次确认。
4. 仅删除状态文件与 manifest 共同证明归属本项目的资源。
5. 删除安装器生成的配置、证书和数据目录；不得影响其他 Compose project、镜像或卷。

任何资源归属无法证明时，保留该资源并报告人工清理步骤。

## 17. 发布与验收门禁

发布流水线必须产出在线包、离线包、SBOM、第三方许可证清单、`SHA256SUMS` 及其签名，并在干净 Windows 环境执行安装 E2E。

以下任一项失败即为发布阻断：敏感信息扫描、manifest 签名、文件或镜像摘要、镜像平台、SBOM、未豁免 Critical/High 漏洞、唯一端口约束、离线安装、升级回滚、卸载资源边界、本地 CA 生命周期或 Windows 干净机 E2E。

详细可执行验收条目见 `docs/installer/ACCEPTANCE_MATRIX.md`。验收证据必须包含命令/步骤、环境、版本、时间和可复核结果，不得以“人工看起来正常”替代自动化证据。

## 18. 失败恢复总则

- 失败后首先保留最后成功状态、数据和可诊断信息；不得通过删除卷“恢复”。
- 重试前重新验证所有易变条件，不盲信旧状态。
- 签名、摘要、状态文件归属或备份合法性失败时禁止继续产生副作用。
- 可自动回滚的操作必须验证回滚后的健康；不能验证即进入需要人工恢复的安全停止态。
- 用户始终可用 `status` 和 `doctor` 获得不含秘密的下一步指引。
