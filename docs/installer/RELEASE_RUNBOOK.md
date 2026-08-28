# Kanyikan Windows 发行 Runbook

适用范围：`docs/installer/INSTALLER_SPEC.md` 契约版本 1、Windows 10/11 x64 本地设备发行。

在线包契约已冻结：`Kanyikan-vX.Y.Z-windows-amd64-online.zip` 只负责下载同版本完整离线 ZIP，验证顶层 `SHA256SUMS.sig` 与离线 ZIP 的 SHA256，然后调用离线包内同一个 `install.cmd`。它不直接拉取镜像，也不维护第二套安装协议。

Tag 工作流先生成并验证 `release-candidate`，完成三轮真实 Windows 验收后才创建草稿 GitHub Release；全部资产上传成功后才公开。禁止把候选制品改名后人工发布。

## 1. 仓库配置

Tag 发行使用以下 GitHub Actions Secrets：

- `WINDOWS_RELEASE_PRIVATE_KEY_PEM`：至少 3072 位 RSA 私钥 PEM。
- `WINDOWS_RELEASE_PUBLIC_KEY_PEM`：与私钥严格配对的 RSA 公钥 PEM。

私钥只允许进入 GitHub 托管的候选组装作业，临时文件权限必须为当前用户只读写，使用后立即删除。日志、步骤摘要、Actions 制品和 Windows E2E 证据均不得输出私钥，也不得输出任何私钥摘要以外的可利用材料。

发行策略位于 `packaging/windows/release-policy.json`：

- `keyId` 标识当前发行信任链。
- `migrationStrategy` 只允许 `none` 或 `alembic_upgrade_head`。
- `supportedFrom` 必须由版本负责人显式维护；不得由流水线猜测可升级版本。

轮换发行密钥时必须更新两个 Secrets 和 `keyId`，构建一个不发布的候选包，独立验证公私钥匹配、包内公钥规范化指纹、manifest 签名及顶层 `SHA256SUMS.sig`。已安装设备不会自动信任新密钥；信任链迁移必须另立方案，不能直接覆盖。

## 2. 专用 Windows Runner

只需准备一台可恢复黄金快照的 Windows 11 x64 虚拟机。三轮验收在这台机器上串行执行，但每轮都必须恢复到同一只读黄金快照、生成新的注册代次，并以一次性 runner 重新注册；普通连续执行三次不算独立纯净验收。

基础标签为：

`self-hosted, Windows, X64, kanyikan-clean-e2e`

每次注册还必须且只能增加当前轮次标签之一：`kanyikan-clean-e2e-round-1`、`kanyikan-clean-e2e-round-2`、`kanyikan-clean-e2e-round-3`。runner 配置命令必须使用 `--ephemeral`，确保一个注册代次只领取一个作业。

基础条件：

- Windows PowerShell 5.1 或更高版本，以管理员身份运行 runner 服务。
- Docker Desktop 已安装，默认使用 Linux Containers，Docker Compose 为 v2。
- 至少 4 个逻辑处理器、8 GiB 内存和 20 GiB 可用安装空间。
- 中文区域可用；临时安装路径会同时包含中文和空格。
- 机器级环境变量 `KANYIKAN_CLEAN_E2E=1`。不得在开发机或含业务数据的长期 runner 上设置该标记。
- 黄金快照内不得存在 `%ProgramData%\KanyikanCleanE2E\snapshot-consumed.json`、Kanyikan 安装状态、发行包、容器、业务卷或六个发行镜像缓存。
- 每轮结束后停止虚拟机并恢复黄金快照；不能删除工作目录或手工删除消费标记来冒充快照复原。

Runner 进程环境必须提供：

- `KANYIKAN_CLEAN_E2E_GENERATION_ID`：每次恢复黄金快照后由宿主基础设施生成的新 UUID，三轮不得重复。
- `KANYIKAN_CLEAN_SNAPSHOT_SHA256`：黄金快照不可变标识的 64 位小写 SHA256，三轮必须相同。
- `KANYIKAN_CLEAN_E2E_ROUND`：当前注册轮次，只允许 `1`、`2` 或 `3`，并必须与轮次标签一致。
- `KANYIKAN_ENTER_OFFLINE_SCRIPT`：在不终止当前 PowerShell 步骤的前提下隔离公网及 Registry；Docker Engine 本机控制面仍须可用。
- `KANYIKAN_EXIT_OFFLINE_SCRIPT`：无条件恢复上一项隔离造成的网络状态。
- `KANYIKAN_INFRASTRUCTURE_HOOKS_ROOT`：负向场景 Hook 所在绝对目录。

断网 Hook 由 runner 基础设施负责人实现并在接入前单独演练。进入脚本返回 `0` 后，`https://example.com/` 必须不可达；退出脚本返回 `0` 后，runner 必须恢复 GitHub Actions 通信。不得通过修改被测安装包、DNS 返回假成功或保留 Registry 缓存来模拟断网。

单机轮转顺序固定为：恢复黄金快照 → 设置本轮三个环境变量 → 用当前轮次标签和 `--ephemeral` 注册 → 启动 runner 并等待这一轮作业结束 → 关闭虚拟机。流水线会在作业开始时写入 `snapshot-consumed.json`；若下一轮仍看到该文件会立即失败。公开 Release 前还会汇总三份 `runner-rotation.json`，要求三轮编号完整、三个注册代次互异、黄金快照 SHA256 唯一且 Tag/commit 一致。

## 3. 负向场景 Hook 契约

`KANYIKAN_INFRASTRUCTURE_HOOKS_ROOT` 必须包含以下脚本，全部以管理员权限执行：

- `Enter-DockerStopped.ps1` 与 `Exit-DockerStopped.ps1`：停止并恢复 Docker Engine，退出时等待 Linux Engine 就绪。
- `Enter-WindowsContainers.ps1` 与 `Exit-WindowsContainers.ps1`：切换到 Windows Containers，再恢复 Linux Containers；两端都必须等待目标 Engine 就绪。
- `Enter-DiskInsufficient.ps1` 与 `Exit-DiskInsufficient.ps1`：挂载独立的 NTFS 测试卷，使可用空间严格少于 20 GiB。进入脚本最后一行必须输出单行 JSON，例如 `{"volumeRoot":"R:\\"}`；退出脚本卸载该卷并清理专用 VHD。

Hook 可以输出诊断信息，但最后一行 JSON 规则只适用于磁盘进入脚本。Hook 不得结束 10443 端口占用进程、删除 Kanyikan 以外的容器/网络/卷、输出代理凭据或更改发行包。任何进入 Hook 被调用后，退出 Hook 都会在 `finally` 中执行；退出失败按发布失败处理。

## 4. Tag 与候选流水线

只接受严格 `vX.Y.Z` Tag，不接受前导零、预发布后缀或构建元数据。流水线依次执行：

1. 后端全量测试、`pip-audit`、前端依赖审计、构建和 Playwright E2E。
2. 构建 Backend、Frontend、Nginx 三个 `linux/amd64` 镜像；按固定 digest 镜像化 PostgreSQL、Redis、Browserless。
3. 六镜像分别生成 SPDX SBOM 和 Grype JSON，执行 Critical/High 豁免门禁，完成 Cosign 签名、SBOM 证明和 GitHub 来源证明。
4. 生成第三方许可证清单、恰含六个顶层 digest 的 OCI 归档和 RSA 签名离线 ZIP。
5. 使用独立验证器复核离线 ZIP 的成员边界、Schema、公钥指纹、manifest 签名、文件摘要、镜像摘要、平台和秘密扫描。
6. 生成在线引导 ZIP，并用另一独立验证器复核固定信任锚、成员集合、下载/验签/解压/调用离线安装器契约。
7. 生成 `Kanyikan-vX.Y.Z-SBOM.zip`、`SHA256SUMS` 及 `SHA256SUMS.sig`；在线与离线 ZIP 必须同时进入已签名摘要并立即复核。
8. 上传 `release-candidate`，随后由同一台 Windows 虚拟机经三次黄金快照复原串行执行完全断网安装。
9. 汇总三轮快照代次证据，再次验证顶层签名和全部资产摘要，创建草稿 GitHub Release；全部上传成功后公开。

任一作业、矩阵项、漏洞门禁、签名复核或 Windows 验收失败，后续依赖作业不会运行。

## 5. Windows 验收与证据

每轮正向 E2E 必须证明：六镜像缓存为空、完全断网安装成功、10 个服务健康、唯一入口为 `https://127.0.0.1:10443`、管理员登录与核心 API 冒烟成功、重启和完整备份成功、CurrentUser Root CA 生命周期正确、默认卸载保留数据、Purge 精确清理。

第一轮还执行端口占用、Docker 停止、Windows Containers、磁盘不足、manifest 篡改、镜像归档损坏，以及在 `IMAGES_LOADED`、`CONFIG_CREATED`、`SERVICES_STARTING` 三个状态中断后重试和重复安装。

Actions 制品至少包括：

- `release-candidate`：在线 ZIP、离线 ZIP、SBOM ZIP、第三方许可证、`SHA256SUMS`、签名和候选状态。
- `windows-e2e-round-1`：第一轮正向、负向 JSON 及全部脱敏控制器输出。
- `windows-e2e-round-2`、`windows-e2e-round-3`：各自独立的正向 JSON 及脱敏输出。

证据 JSON 必须记录 Tag 对应 commit、发行版本、Windows/PowerShell/Docker/Compose 版本、ZIP 与 manifest 摘要、精确退出码、开始/结束时间，以及可随制品移动的相对输出路径。控制器输出在落盘前扫描密码、Token、JWT、认证 URL、Cookie 和 PEM 私钥；命中任何模式即阻断。

## 6. 当前 Go/No-Go

以下外部条件全部满足前，结论保持 No-Go：

- 仓库已配置配对 RSA Secrets，Tag 工作流在真实 GitHub 环境完整执行。
- 三轮真实 Windows 11 证据均来自同一黄金快照的三个独立 ephemeral 注册代次，且所有 P0/P1 发布门通过。
- 负责人与安全复核人共同核对制品名称、摘要、证据和密钥标识。

正式发布作业已经依赖上述门禁，并使用同一 Tag 创建草稿 GitHub Release；任何上传失败都保持非公开状态，全部成功后才公开。禁止从失败或取消的 Actions 运行中手工挑选制品发布。
