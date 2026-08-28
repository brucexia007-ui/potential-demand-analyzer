[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command,

    [string]$Package,

    [string]$Backup,

    [switch]$PurgeData
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$script:Entrypoint = 'https://127.0.0.1:10443'
$script:TrustedReleasePublicKeySha256 = '__KANYIKAN_RELEASE_PUBLIC_KEY_SHA256__'
$script:InstallRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$script:State = $null
$script:Stage = 'COMMAND'

function Write-KanyikanResult {
    param([string]$Level, [string]$Message)
    Write-Host "[$Level] $Message"
}

function Stop-KanyikanCommand {
    param([int]$ExitCode, [string]$Reason, [string]$NextStep)

    if ($null -ne $script:State -and $script:State.currentState -cne 'NEW' -and [System.IO.File]::Exists((Get-KanyikanStatePath -InstallRoot $script:InstallRoot))) {
        try {
            $script:State = Set-KanyikanInstallFailure -State $script:State -Command $Command -Stage $script:Stage -ExitCode $ExitCode -Reason $Reason -InstallRoot $script:InstallRoot
        }
        catch { }
    }
    Write-KanyikanResult -Level '失败' -Message "命令=$Command；阶段=$script:Stage；退出码=$ExitCode；原因=$Reason"
    Write-KanyikanResult -Level '下一步' -Message $NextStep
    exit $ExitCode
}

function Get-StateIndex {
    param([string]$StateName)
    return [Array]::IndexOf((Get-KanyikanInstallStates), $StateName)
}

function Test-OwnedEntrypoint {
    if ((Get-StateIndex -StateName ([string]$script:State.currentState)) -lt (Get-StateIndex -StateName 'SERVICES_STARTING')) { return $false }
    try {
        $facts = @(Get-KanyikanServiceFacts -InstallRoot $script:InstallRoot)
        $nginx = @($facts | Where-Object { $_.Service -ceq 'nginx' })
        if ($nginx.Count -ne 1) { return $false }
        $publishers = @($nginx[0].Publishers)
        return $publishers.Count -eq 1 -and [string]$publishers[0].URL -ceq '127.0.0.1' -and [int]$publishers[0].PublishedPort -eq 10443
    }
    catch { return $false }
}

function Invoke-Install {
    $script:Stage = 'STATE'
    try { $script:State = Read-KanyikanInstallState -InstallRoot $script:InstallRoot }
    catch { Stop-KanyikanCommand -ExitCode 90 -Reason $_.Exception.Message -NextStep '请从有效备份恢复 state/install-state.json。' }

    $script:Stage = 'PREFLIGHT'
    $preflight = Invoke-KanyikanPreflight -InstallRoot $script:InstallRoot -AllowOwnedEntrypoint:(Test-OwnedEntrypoint)
    foreach ($check in $preflight.checks) {
        $mark = if ($check.passed) { '通过' } else { '失败' }
        Write-KanyikanResult -Level $mark -Message $check.name
    }
    if (-not $preflight.passed) { Stop-KanyikanCommand -ExitCode $preflight.exitCode -Reason $preflight.failedCheck -NextStep $preflight.remediation }
    Write-KanyikanResult -Level '信息' -Message "Docker 代理已启用=$($preflight.proxyEnabled)（不记录代理地址或凭据）"

    $script:Stage = 'VERIFIED'
    try { $release = Test-KanyikanReleasePackage -PackageRoot $script:InstallRoot -TrustedPublicKeySha256 $script:TrustedReleasePublicKeySha256 }
    catch { Stop-KanyikanCommand -ExitCode 30 -Reason $_.Exception.Message -NextStep '请使用官方渠道发布且完整未修改的离线包。' }

    $adminPassword = $null
    if ((Get-StateIndex -StateName ([string]$script:State.currentState)) -lt (Get-StateIndex -StateName 'CONFIG_CREATED')) {
        $script:Stage = 'ADMIN_PASSWORD'
        try { $adminPassword = Read-KanyikanAdminPassword }
        catch { Stop-KanyikanCommand -ExitCode 40 -Reason $_.Exception.Message -NextStep '请重新运行 install 并输入两次相同且符合策略的密码。' }
    }

    if ($script:State.currentState -ceq 'NEW') {
        $script:State.productVersion = $release.version
        $script:State.manifestSha256 = $release.manifestSha256
        $script:State.releasePublicKeySha256 = $release.releasePublicKeySha256
        $script:State = Set-KanyikanInstallState -State $script:State -NextState 'PREFLIGHT_OK' -InstallRoot $script:InstallRoot
    }
    if ($script:State.currentState -ceq 'PREFLIGHT_OK') {
        $script:State.productVersion = $release.version
        $script:State.manifestSha256 = $release.manifestSha256
        $script:State.releasePublicKeySha256 = $release.releasePublicKeySha256
        $script:State = Set-KanyikanInstallState -State $script:State -NextState 'VERIFIED' -InstallRoot $script:InstallRoot
    }

    $script:Stage = 'IMAGES_LOADED'
    try {
        if ($script:State.currentState -ceq 'VERIFIED') {
            $script:State.images = @(Import-KanyikanReleaseImages -Manifest $release.manifest -PackageRoot $script:InstallRoot)
            $script:State = Set-KanyikanInstallState -State $script:State -NextState 'IMAGES_LOADED' -InstallRoot $script:InstallRoot
        }
        else { [void](Test-KanyikanReleaseImagesPresent -Manifest $release.manifest) }
    }
    catch { Stop-KanyikanCommand -ExitCode 31 -Reason $_.Exception.Message -NextStep '请检查 Docker Engine，或重新获取完整离线包。' }

    $environmentPath = [System.IO.Path]::Combine($script:InstallRoot, 'config', 'system.env')
    $script:Stage = 'CONFIG_CREATED'
    try {
        if ($script:State.currentState -ceq 'IMAGES_LOADED') {
            Write-KanyikanSystemEnvironment -TemplatePath ([System.IO.Path]::Combine($script:InstallRoot, 'config', 'system.env.template')) -DestinationPath $environmentPath -Manifest $release.manifest -AdminPassword $adminPassword
            $adminPassword = $null
            $script:State = Set-KanyikanInstallState -State $script:State -NextState 'CONFIG_CREATED' -InstallRoot $script:InstallRoot
        }
        elseif (-not (Test-KanyikanSystemEnvironment -Path $environmentPath -Manifest $release.manifest)) { throw '现有 system.env 内容或 ACL 复核失败。' }
    }
    catch { $adminPassword = $null; Stop-KanyikanCommand -ExitCode 40 -Reason $_.Exception.Message -NextStep '请检查 config 目录权限后重试；不要删除已有 system.env。' }

    $certificateDirectory = [System.IO.Path]::Combine($script:InstallRoot, 'config', 'certs')
    $script:Stage = 'CERT_READY'
    try {
        if ($script:State.currentState -ceq 'CONFIG_CREATED') {
            $certificate = New-KanyikanLocalCertificate -Manifest $release.manifest -CertificateDirectory $certificateDirectory
            $script:State.caThumbprint = $certificate.caThumbprint
            $answer = Read-Host '是否信任本机独立根证书？输入 TRUST 表示同意，其他输入表示拒绝'
            $trustedThumbprint = Install-KanyikanLocalRootTrust -CertificatePath ([System.IO.Path]::Combine($certificateDirectory, 'local-root-ca.crt')) -Consent ($answer -ceq 'TRUST')
            $script:State.caTrusted = $null -ne $trustedThumbprint
            $script:State = Set-KanyikanInstallState -State $script:State -NextState 'CERT_READY' -InstallRoot $script:InstallRoot
            if (-not $script:State.caTrusted) { Write-KanyikanResult -Level '提示' -Message '已拒绝信任本地 CA；服务仍会启动，但浏览器会显示证书警告。' }
        }
        else { [void](Test-KanyikanLocalCertificate -Manifest $release.manifest -CertificateDirectory $certificateDirectory) }
    }
    catch { Stop-KanyikanCommand -ExitCode 41 -Reason $_.Exception.Message -NextStep '请检查证书目录权限与 Docker 后重试。' }

    $script:Stage = 'SERVICES_STARTING'
    try {
        if ($script:State.currentState -ceq 'CERT_READY' -or $script:State.currentState -ceq 'SERVICES_STARTING') {
            Start-KanyikanServices -InstallRoot $script:InstallRoot
            if ($script:State.currentState -ceq 'CERT_READY') { $script:State = Set-KanyikanInstallState -State $script:State -NextState 'SERVICES_STARTING' -InstallRoot $script:InstallRoot }
        }
    }
    catch { Stop-KanyikanCommand -ExitCode 50 -Reason $_.Exception.Message -NextStep '请运行 status 检查服务状态后重试。' }

    $script:Stage = 'HEALTHY'
    $ready = Wait-KanyikanBootstrapReady -InstallRoot $script:InstallRoot
    if (-not $ready.passed) { Stop-KanyikanCommand -ExitCode 51 -Reason $ready.reason -NextStep '请保留容器并运行 status；安装器不会删除数据卷。' }
    if ($script:State.currentState -ceq 'SERVICES_STARTING') { $script:State = Set-KanyikanInstallState -State $script:State -NextState 'HEALTHY' -InstallRoot $script:InstallRoot }
    if ($script:State.currentState -ceq 'HEALTHY') { $script:State = Set-KanyikanInstallState -State $script:State -NextState 'INSTALLED' -InstallRoot $script:InstallRoot }

    Write-KanyikanResult -Level '成功' -Message "Kanyikan $($script:State.productVersion) 已安装。"
    Write-KanyikanResult -Level '入口' -Message $script:Entrypoint
    try { Start-Process $script:Entrypoint } catch { Write-KanyikanResult -Level '提示' -Message '无法自动打开浏览器，请手动访问上方入口。' }
}

function Invoke-Status {
    try { $state = Read-KanyikanInstallState -InstallRoot $script:InstallRoot }
    catch { Stop-KanyikanCommand -ExitCode 90 -Reason $_.Exception.Message -NextStep '请恢复有效安装状态。' }
    Write-KanyikanResult -Level '版本' -Message $(if ($null -eq $state.productVersion) { '未安装' } else { [string]$state.productVersion })
    Write-KanyikanResult -Level '状态' -Message ([string]$state.currentState)
    Write-KanyikanResult -Level '入口' -Message $script:Entrypoint
    try {
        $facts = @(Get-KanyikanServiceFacts -InstallRoot $script:InstallRoot)
        foreach ($fact in $facts) { Write-KanyikanResult -Level '服务' -Message "$($fact.Service): $($fact.State)/$($fact.Health)" }
    }
    catch { Write-KanyikanResult -Level '服务' -Message 'Docker 或 Compose 状态不可用。' }
}

if ($PSVersionTable.PSVersion -lt [Version]'5.1' -or $PSEdition -cne 'Desktop') {
    Write-Host '[失败] 仅支持 Windows PowerShell 5.1 或更高版本。'
    exit 20
}
if ([string]::IsNullOrWhiteSpace($Command)) { Write-Host '[失败] 必须指定命令。'; exit 10 }

$modulePath = [System.IO.Path]::Combine($script:InstallRoot, 'lib', 'Kanyikan.Installer.psm1')
try { Import-Module $modulePath -Force }
catch { Write-Host '[失败] 无法加载安装器模块。'; exit 90 }

switch -CaseSensitive ($Command.ToLowerInvariant()) {
    'install' { Invoke-Install; exit 0 }
    'status' { Invoke-Status; exit 0 }
    'start' {
        $script:State = Read-KanyikanInstallState -InstallRoot $script:InstallRoot
        if ((Get-StateIndex $script:State.currentState) -lt (Get-StateIndex 'CERT_READY')) { Stop-KanyikanCommand -ExitCode 10 -Reason '尚未完成配置和证书阶段。' -NextStep '请先运行 install。' }
        try { Start-KanyikanServices -InstallRoot $script:InstallRoot; $ready = Wait-KanyikanBootstrapReady -InstallRoot $script:InstallRoot; if (-not $ready.passed) { throw $ready.reason } }
        catch { Stop-KanyikanCommand -ExitCode 51 -Reason $_.Exception.Message -NextStep '请运行 status 后重试。' }
        Write-KanyikanResult -Level '成功' -Message "服务已启动：$script:Entrypoint"; exit 0
    }
    'stop' {
        $script:State = Read-KanyikanInstallState -InstallRoot $script:InstallRoot
        if ($script:State.currentState -ceq 'NEW') { Stop-KanyikanCommand -ExitCode 10 -Reason '没有安装状态。' -NextStep '请先运行 install。' }
        try { Stop-KanyikanServices -InstallRoot $script:InstallRoot }
        catch { Stop-KanyikanCommand -ExitCode 50 -Reason $_.Exception.Message -NextStep '请检查 Docker Desktop。' }
        Write-KanyikanResult -Level '成功' -Message '服务已停止，数据和配置均已保留。'; exit 0
    }
    'restart' {
        $script:State = Read-KanyikanInstallState -InstallRoot $script:InstallRoot
        try { Stop-KanyikanServices -InstallRoot $script:InstallRoot; Start-KanyikanServices -InstallRoot $script:InstallRoot; $ready = Wait-KanyikanBootstrapReady -InstallRoot $script:InstallRoot; if (-not $ready.passed) { throw $ready.reason } }
        catch { Stop-KanyikanCommand -ExitCode 51 -Reason $_.Exception.Message -NextStep '请运行 status 后重试。' }
        Write-KanyikanResult -Level '成功' -Message "服务已重启：$script:Entrypoint"; exit 0
    }
    'backup' {
        $script:State = Read-KanyikanInstallState -InstallRoot $script:InstallRoot
        if ($script:State.currentState -cne 'INSTALLED') { Stop-KanyikanCommand -ExitCode 10 -Reason '尚未完成安装。' -NextStep '请先运行 install。' }
        $script:Stage = 'BACKUP'
        try {
            Start-KanyikanServices -InstallRoot $script:InstallRoot
            $ready = Wait-KanyikanBootstrapReady -InstallRoot $script:InstallRoot
            if (-not $ready.passed) { throw $ready.reason }
            $result = Invoke-KanyikanBackup -InstallRoot $script:InstallRoot -State $script:State
        }
        catch { Stop-KanyikanCommand -ExitCode 60 -Reason $_.Exception.Message -NextStep '请保留无 VALID 标志的失败现场并检查磁盘、数据库和数据卷。' }
        Write-KanyikanResult -Level '成功' -Message "完整备份已校验：$($result.path)"
        Write-KanyikanResult -Level '重要' -Message '请另行安全保存 config/system.env；缺少该文件将无法恢复已加密的 Provider 配置。'
        exit 0
    }
    'restore' {
        $script:State = Read-KanyikanInstallState -InstallRoot $script:InstallRoot
        if ($script:State.currentState -cne 'INSTALLED') { Stop-KanyikanCommand -ExitCode 10 -Reason '尚未完成安装。' -NextStep '请先运行 install。' }
        if ([string]::IsNullOrWhiteSpace($Backup)) { Stop-KanyikanCommand -ExitCode 10 -Reason 'restore 必须指定 -Backup。' -NextStep '请传入 data/backups 内的完整备份目录。' }
        $script:Stage = 'RESTORE_VALIDATE'
        try { $validated = Invoke-KanyikanValidateBackup -InstallRoot $script:InstallRoot -State $script:State -BackupPath $Backup }
        catch { Stop-KanyikanCommand -ExitCode 61 -Reason $_.Exception.Message -NextStep '请选择当前版本生成且带 VALID 标志的完整备份。' }
        $confirmation = Read-Host "恢复将覆盖当前数据库、快照和 Skill。请输入 RESTORE $($validated.name)"
        if ($confirmation -cne "RESTORE $($validated.name)") { Stop-KanyikanCommand -ExitCode 10 -Reason '恢复确认文本不匹配。' -NextStep '未修改任何数据；需要恢复时重新运行命令。' }
        $script:Stage = 'RESTORE_PROTECTION_BACKUP'
        try { $protection = Invoke-KanyikanBackup -InstallRoot $script:InstallRoot -State $script:State }
        catch { Stop-KanyikanCommand -ExitCode 60 -Reason $_.Exception.Message -NextStep '保护备份失败，恢复未开始。' }
        $script:Stage = 'RESTORE'
        try {
            Stop-KanyikanServices -InstallRoot $script:InstallRoot
            [void](Invoke-KanyikanRestore -InstallRoot $script:InstallRoot -State $script:State -BackupName $validated.name)
            Start-KanyikanServices -InstallRoot $script:InstallRoot
            $ready = Wait-KanyikanBootstrapReady -InstallRoot $script:InstallRoot
            if (-not $ready.passed) { throw $ready.reason }
        }
        catch {
            try { Stop-KanyikanServices -InstallRoot $script:InstallRoot } catch { }
            Stop-KanyikanCommand -ExitCode 61 -Reason $_.Exception.Message -NextStep "入口已停止；恢复前保护备份保留在 $($protection.path)，请人工处理后重试。"
        }
        Write-KanyikanResult -Level '成功' -Message "已从 $($validated.path) 恢复并通过健康检查。"
        Write-KanyikanResult -Level '保护备份' -Message $protection.path
        exit 0
    }
    'doctor' {
        $script:Stage = 'DOCTOR'
        try { $report = Get-KanyikanDoctorReport -InstallRoot $script:InstallRoot }
        catch { Stop-KanyikanCommand -ExitCode 90 -Reason $_.Exception.Message -NextStep '请检查安装状态与 Docker Desktop。' }
        Write-KanyikanResult -Level '诊断时间' -Message $report.generatedAt
        Write-KanyikanResult -Level '安装状态' -Message $report.installState
        foreach ($check in $report.checks) { Write-KanyikanResult -Level $check.status -Message "$($check.name)：$($check.detail)" }
        Write-KanyikanResult -Level '入口' -Message $report.entrypoint
        exit 0
    }
    'support-bundle' {
        $script:Stage = 'SUPPORT_BUNDLE'
        try { $bundle = Export-KanyikanSupportBundle -InstallRoot $script:InstallRoot }
        catch { Stop-KanyikanCommand -ExitCode 90 -Reason $_.Exception.Message -NextStep '请运行 doctor；敏感信息扫描失败时不会交付支持包。' }
        Write-KanyikanResult -Level '成功' -Message "脱敏支持包已生成：$($bundle.path)"
        exit 0
    }
    default { Write-Host "[失败] 未知或尚未实现的命令：$Command"; exit 10 }
}
