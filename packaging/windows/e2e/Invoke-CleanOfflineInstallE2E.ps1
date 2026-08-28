[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OfflineZip,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedPublicKeySha256,
    [Parameter(Mandatory = $true)][string]$EnterOfflineScript,
    [Parameter(Mandatory = $true)][string]$ExitOfflineScript,
    [Parameter(Mandatory = $true)][string]$EvidencePath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module (Join-Path $PSScriptRoot 'Kanyikan.ReleaseE2E.psm1') -Force

if ([Environment]::GetEnvironmentVariable('KANYIKAN_CLEAN_E2E', 'Machine') -cne '1') {
    throw '只允许在设置机器级 KANYIKAN_CLEAN_E2E=1 的专用一次性 runner 上执行。'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Windows 发行 E2E 必须以管理员身份运行。'
}

$offlineZipPath = (Resolve-Path -LiteralPath $OfflineZip).Path
$enterOfflinePath = (Resolve-Path -LiteralPath $EnterOfflineScript).Path
$exitOfflinePath = (Resolve-Path -LiteralPath $ExitOfflineScript).Path
$evidenceFullPath = [System.IO.Path]::GetFullPath($EvidencePath)
$evidenceDirectory = [System.IO.Path]::GetDirectoryName($evidenceFullPath)
$controllerOutputDirectory = [System.IO.Path]::Combine($evidenceDirectory, ([System.IO.Path]::GetFileNameWithoutExtension($evidenceFullPath) + '-controller-output'))
[System.IO.Directory]::CreateDirectory($evidenceDirectory) | Out-Null

$startedAt = [DateTime]::UtcNow
$checks = New-Object 'System.Collections.Generic.List[object]'
$controllerRuns = New-Object 'System.Collections.Generic.List[object]'
$installParent = [System.IO.Path]::Combine($env:RUNNER_TEMP, "看一看 E2E 安装 $([Guid]::NewGuid().ToString('N'))")
$wrapperPath = Join-Path $PSScriptRoot 'Invoke-ControllerWithAnswers.ps1'
$offlineEntered = $false
$packageRoot = $null
$release = $null
$failure = $null

function Add-Check([string]$Name, [string]$Status, [string]$Detail) {
    $checks.Add([pscustomobject][ordered]@{ name = $Name; status = $Status; detail = $Detail })
}

function Get-ControllerOutputEvidencePath([string]$Path) {
    return ([IO.Path]::Combine([IO.Path]::GetFileName($controllerOutputDirectory), [IO.Path]::GetFileName($Path))).Replace('\', '/')
}

function Invoke-Guard([string]$Path) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Path
    if ($LASTEXITCODE -ne 0) { throw "离线网络守卫脚本失败：$Path；退出码=$LASTEXITCODE" }
}

function Invoke-Controller {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string]$Package,
        [string]$Backup,
        [switch]$PurgeData,
        [int[]]$AllowedExitCodes = @(0)
    )
    $controllerPath = [System.IO.Path]::Combine($packageRoot, 'kanyikan.ps1')
    foreach ($value in @($wrapperPath, $controllerPath, $Package, $Backup)) {
        if (-not [string]::IsNullOrEmpty($value) -and $value.Contains('"')) { throw 'E2E 路径不得包含双引号。' }
    }
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$wrapperPath`"", '-ControllerPath', "`"$controllerPath`"", '-Command', $Command)
    if (-not [string]::IsNullOrWhiteSpace($Package)) { $arguments += @('-Package', "`"$Package`"") }
    if (-not [string]::IsNullOrWhiteSpace($Backup)) { $arguments += @('-Backup', "`"$Backup`"") }
    if ($PurgeData) { $arguments += '-PurgeData' }
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'powershell.exe'
    $startInfo.Arguments = $arguments -join ' '
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.EnvironmentVariables['KANYIKAN_E2E_ADMIN_PASSWORD'] = $script:AdminPassword
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw "无法启动控制器命令：$Command" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        if ($stdout.Contains($script:AdminPassword) -or $stderr.Contains($script:AdminPassword)) { throw "控制器输出泄露 E2E 管理员密码：$Command" }
        $outputSha256 = Get-StringSha256 -Value ($stdout + "`n" + $stderr)
        $outputRecord = Write-KanyikanE2EControllerOutput -Run ([pscustomobject]@{
            stdout = $stdout
            stderr = $stderr
            outputSha256 = $outputSha256
        }) -OutputDirectory $controllerOutputDirectory -Name (("{0:D2}-{1}" -f ($controllerRuns.Count + 1), $Command))
        $controllerRuns.Add([pscustomobject][ordered]@{
            command = $Command
            exitCode = $process.ExitCode
            outputSha256 = $outputSha256
            outputPath = Get-ControllerOutputEvidencePath -Path $outputRecord.path
        })
        if ($AllowedExitCodes -notcontains $process.ExitCode) { throw "控制器命令失败：$Command；退出码=$($process.ExitCode)" }
        return [pscustomobject]@{ exitCode = $process.ExitCode; stdout = $stdout; stderr = $stderr }
    }
    finally { $process.Dispose() }
}

function Get-StringSha256([string]$Value) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-FileSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Test-VolumeExists([string]$Name) {
    & docker volume inspect $Name *> $null
    return $LASTEXITCODE -eq 0
}

$random = New-Object byte[] 24
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $generator.GetBytes($random) } finally { $generator.Dispose() }
$script:AdminPassword = "E2E-$([Convert]::ToBase64String($random).Replace('/', '_').Replace('+', '-'))!aA1"

try {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($offlineZipPath)
    try {
        $roots = @($archive.Entries | Where-Object { -not [string]::IsNullOrWhiteSpace($_.FullName) } | ForEach-Object { $_.FullName.Split('/')[0] } | Sort-Object -Unique)
        if ($roots.Count -ne 1 -or $roots[0] -notmatch '^Kanyikan-v.+-windows-amd64$') { throw '离线 ZIP 顶层发行目录不合法。' }
        $packageDirectoryName = $roots[0]
    }
    finally { $archive.Dispose() }
    [IO.Directory]::CreateDirectory($installParent) | Out-Null
    [IO.Compression.ZipFile]::ExtractToDirectory($offlineZipPath, $installParent)
    $packageRoot = [IO.Path]::Combine($installParent, $packageDirectoryName)
    if (-not $packageRoot.Contains(' ') -or -not $packageRoot.Contains('看一看')) { throw 'E2E 安装路径未同时覆盖中文与空格。' }

    $modulePath = [IO.Path]::Combine($packageRoot, 'lib', 'Kanyikan.Installer.psm1')
    Import-Module $modulePath -Force
    $release = Test-KanyikanReleasePackage -PackageRoot $packageRoot -TrustedPublicKeySha256 $ExpectedPublicKeySha256
    Add-Check '发行包签名与摘要' '通过' $release.version

    $imageReferences = @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless') | ForEach-Object { [string]$release.manifest.images.$_.reference }
    foreach ($reference in $imageReferences) { & docker image rm --force $reference *> $null }
    foreach ($reference in $imageReferences) {
        & docker image inspect $reference *> $null
        if ($LASTEXITCODE -eq 0) { throw "无法清空发行镜像缓存：$reference" }
    }
    Add-Check '六镜像缓存为空' '通过' '安装前六个精确 digest 均不可用'

    Invoke-Guard -Path $enterOfflinePath
    $offlineEntered = $true
    $publicNetworkReachable = $false
    try { Invoke-WebRequest -UseBasicParsing -Uri 'https://example.com/' -TimeoutSec 5 | Out-Null; $publicNetworkReachable = $true } catch { }
    if ($publicNetworkReachable) { throw '离线网络守卫未阻断公网访问。' }
    & docker info --format '{{.OSType}}/{{.Architecture}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '进入离线模式后 Docker Engine 不可用。' }
    Add-Check '完全断网' '通过' '公网探测失败且本机 Docker Engine 可用'

    Invoke-Controller -Command 'install' | Out-Null
    Add-Check '离线安装命令' '通过' '退出码 0'
}
catch { $failure = $_.Exception.Message }
finally {
    if ($offlineEntered) {
        try { Invoke-Guard -Path $exitOfflinePath; $offlineEntered = $false }
        catch { if ($null -eq $failure) { $failure = $_.Exception.Message } else { $failure += "；恢复网络失败：$($_.Exception.Message)" } }
    }
}

try {
    if ($null -ne $failure) { throw $failure }
    $state = Read-KanyikanInstallState -InstallRoot $packageRoot
    if ($state.currentState -cne 'INSTALLED' -or [string]$state.productVersion -cne [string]$release.version) { throw '安装状态不是目标版本 INSTALLED。' }
    $serviceResult = Test-KanyikanServiceFacts -Facts @(Get-KanyikanServiceFacts -InstallRoot $packageRoot)
    if (-not $serviceResult.passed) { throw $serviceResult.reason }
    Test-KanyikanAuthenticatedSmoke -InstallRoot $packageRoot
    Add-Check '10 服务、唯一端口与认证冒烟' '通过' '仅 127.0.0.1:10443；管理员登录和核心 API 成功'

    Invoke-Controller -Command 'restart' | Out-Null
    $ready = Wait-KanyikanBootstrapReady -InstallRoot $packageRoot
    if (-not $ready.passed) { throw $ready.reason }
    Add-Check '服务重启' '通过' 'Bootstrap Ready'

    Invoke-Controller -Command 'backup' | Out-Null
    $state = Read-KanyikanInstallState -InstallRoot $packageRoot
    $backup = Get-KanyikanLatestValidBackup -InstallRoot $packageRoot -State $state
    if ($null -eq $backup) { throw '未找到控制器刚生成的有效完整备份。' }
    Add-Check '完整备份' '通过' $backup.name

    $thumbprint = [string]$state.caThumbprint
    $rootStore = New-Object Security.Cryptography.X509Certificates.X509Store([Security.Cryptography.X509Certificates.StoreName]::Root, [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
    try { $rootStore.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly); if (@($rootStore.Certificates | Where-Object { $_.Thumbprint -ceq $thumbprint }).Count -ne 1) { throw '本地 CA 未出现在 CurrentUser Root。' } }
    finally { $rootStore.Close() }
    Add-Check '本地 CA 安装' '通过' $thumbprint

    Invoke-Controller -Command 'uninstall' | Out-Null
    foreach ($volume in @('kanyikan_postgres_data', 'kanyikan_redis_data', 'kanyikan_snapshots_data', 'kanyikan_skills_data')) {
        if (-not (Test-VolumeExists -Name $volume)) { throw "默认卸载误删数据卷：$volume" }
    }
    $rootStore = New-Object Security.Cryptography.X509Certificates.X509Store([Security.Cryptography.X509Certificates.StoreName]::Root, [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
    try { $rootStore.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly); if (@($rootStore.Certificates | Where-Object { $_.Thumbprint -ceq $thumbprint }).Count -ne 0) { throw '默认卸载未删除本地 CA 信任。' } }
    finally { $rootStore.Close() }
    Add-Check '默认卸载' '通过' '数据卷、配置和备份保留；CurrentUser Root 信任已删除'

    Invoke-Controller -Command 'start' | Out-Null
    $ready = Wait-KanyikanBootstrapReady -InstallRoot $packageRoot
    if (-not $ready.passed) { throw $ready.reason }
    if (-not [IO.Directory]::Exists($backup.path)) { throw '默认卸载后完整备份丢失。' }
    Add-Check '默认卸载后重新启动' '通过' '入口健康且备份仍存在'

    Invoke-Controller -Command 'uninstall' -PurgeData | Out-Null
    foreach ($volume in @('kanyikan_postgres_data', 'kanyikan_redis_data', 'kanyikan_snapshots_data', 'kanyikan_skills_data')) {
        if (Test-VolumeExists -Name $volume) { throw "Purge 未删除数据卷：$volume" }
    }
    foreach ($relative in @('config/system.env', 'config/certs', 'state', 'data', 'logs')) {
        if ([IO.File]::Exists((Join-Path $packageRoot $relative)) -or [IO.Directory]::Exists((Join-Path $packageRoot $relative))) { throw "Purge 未删除生成路径：$relative" }
    }
    Add-Check 'Purge 卸载' '通过' '四个数据卷和全部安装器生成路径已删除'
}
catch {
    $failure = $_.Exception.Message
    if ($null -ne $packageRoot -and [IO.File]::Exists((Join-Path $packageRoot 'kanyikan.ps1'))) {
        try { Invoke-Controller -Command 'support-bundle' -AllowedExitCodes @(0, 90) | Out-Null } catch { }
    }
}
finally {
    $script:AdminPassword = $null
    $dockerVersion = (& docker version --format '{{.Server.Version}}' 2>$null | Select-Object -First 1)
    $composeVersion = (& docker compose version --short 2>$null | Select-Object -First 1)
    $evidence = [pscustomobject][ordered]@{
        schemaVersion = 1
        startedAt = $startedAt.ToString('o')
        completedAt = [DateTime]::UtcNow.ToString('o')
        passed = $null -eq $failure
        failure = $failure
        sourceCommit = [Environment]::GetEnvironmentVariable('GITHUB_SHA')
        releaseVersion = if ($null -eq $release) { $null } else { [string]$release.version }
        offlineZipSha256 = Get-FileSha256 -Path $offlineZipPath
        manifestSha256 = if ($null -eq $release) { $null } else { [string]$release.manifestSha256 }
        controllerOutputDirectory = [IO.Path]::GetFileName($controllerOutputDirectory)
        environment = [pscustomobject][ordered]@{
            os = [Environment]::OSVersion.VersionString
            powershell = $PSVersionTable.PSVersion.ToString()
            docker = [string]$dockerVersion
            compose = [string]$composeVersion
            installPathCoveredChineseAndSpace = $null -ne $packageRoot -and $packageRoot.Contains('看一看') -and $packageRoot.Contains(' ')
        }
        controllerRuns = @($controllerRuns)
        checks = @($checks)
    }
    [IO.File]::WriteAllText($evidenceFullPath, ($evidence | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
}

if ($null -ne $failure) { Write-Error $failure; exit 1 }
Write-Host "Windows 离线发行 E2E 通过；证据：$evidenceFullPath"
exit 0
