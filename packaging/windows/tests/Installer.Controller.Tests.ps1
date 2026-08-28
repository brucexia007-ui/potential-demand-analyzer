$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$packageRoot = Split-Path $PSScriptRoot -Parent
$controllerPath = Join-Path $packageRoot 'kanyikan.ps1'
$entrypointPath = Join-Path $packageRoot 'install.cmd'
$script:Passed = 0
$script:Failed = 0

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}

Invoke-TestCase '双击入口只转交 install 并保留退出码' {
    $content = [IO.File]::ReadAllText($entrypointPath, [Text.Encoding]::ASCII)
    Assert-True ($content.Contains('powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\kanyikan.ps1" install')) 'install.cmd 未按契约调用控制器。'
    Assert-True ($content.Contains('exit /b %KANYIKAN_EXIT_CODE%')) 'install.cmd 未保留退出码。'
    foreach ($secret in @('ADMIN_PASSWORD', 'SECRET_KEY', 'CONFIG_ENCRYPTION_KEY', 'POSTGRES_PASSWORD')) { Assert-True (-not $content.Contains($secret)) "install.cmd 包含秘密参数 $secret。" }
}

Invoke-TestCase '控制器包含固定信任锚构建占位符' {
    $content = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($content.Contains('__KANYIKAN_RELEASE_PUBLIC_KEY_SHA256__')) '控制器缺少构建时固定信任锚。'
    Assert-True (-not $content.Contains('-TrustedPublicKeySha256 $Package')) '控制器错误接受包内自声明信任锚。'
}

Invoke-TestCase '未知命令退出 10' {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controllerPath not-a-command *> $null
    Assert-True ($LASTEXITCODE -eq 10) '未知命令退出码不是 10。'
}

Invoke-TestCase '缺少命令退出 10' {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controllerPath *> $null
    Assert-True ($LASTEXITCODE -eq 10) '缺少命令退出码不是 10。'
}

Invoke-TestCase '未安装时 status 只读且不创建状态文件' {
    $statePath = Join-Path $packageRoot 'state\install-state.json'
    Assert-True (-not [IO.File]::Exists($statePath)) '测试前存在意外安装状态。'
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controllerPath status 2>&1
    Assert-True ($LASTEXITCODE -eq 0) '未安装 status 未成功。'
    Assert-True (-not [IO.File]::Exists($statePath)) 'status 产生了持久副作用。'
    $joined = @($output) -join "`n"
    Assert-True ($joined.Contains('NEW')) 'status 未显示 NEW。'
    Assert-True ($joined.Contains('https://127.0.0.1:10443')) 'status 未显示唯一入口。'
}

Invoke-TestCase '安装阶段顺序与冻结状态机一致' {
    $content = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    $lastIndex = -1
    foreach ($stage in @("'PREFLIGHT_OK'", "'VERIFIED'", "'IMAGES_LOADED'", "'CONFIG_CREATED'", "'CERT_READY'", "'SERVICES_STARTING'", "'HEALTHY'", "'INSTALLED'")) {
        $index = $content.IndexOf("-NextState $stage", $lastIndex + 1, [StringComparison]::Ordinal)
        Assert-True ($index -gt $lastIndex) "控制器缺少或乱序推进 $stage。"
        $lastIndex = $index
    }
}

Invoke-TestCase '更新严格按冻结事务顺序执行' {
    $content = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    $start = $content.IndexOf('function Invoke-Update', [StringComparison]::Ordinal)
    $end = $content.IndexOf("if (`$PSVersionTable", $start, [StringComparison]::Ordinal)
    $update = $content.Substring($start, $end - $start)
    $lastIndex = -1
    foreach ($step in @(
        'Test-KanyikanReleasePackage -PackageRoot $packageRoot',
        'Test-KanyikanUpgradePath',
        'Stop-KanyikanEntrypoint',
        'Invoke-KanyikanBackup',
        'New-KanyikanReleaseAssetSnapshot',
        'Import-KanyikanReleaseImages',
        'Invoke-KanyikanDatabaseMigration',
        'Start-KanyikanServices',
        'Wait-KanyikanBootstrapReady',
        'Test-KanyikanAuthenticatedSmoke',
        '$script:State.productVersion = $newRelease.version'
    )) {
        $index = $update.IndexOf($step, $lastIndex + 1, [StringComparison]::Ordinal)
        Assert-True ($index -gt $lastIndex) "更新事务缺少或乱序执行：$step"
        $lastIndex = $index
    }
}

Invoke-TestCase '更新回滚恢复完整备份旧资产旧镜像并区分退出码' {
    $content = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    foreach ($required in @(
        'Restore-KanyikanReleaseAssets',
        'Update-KanyikanSystemImageReferences -Path $environmentPath -CurrentManifest $newRelease.manifest -NewManifest $currentRelease.manifest',
        'Invoke-KanyikanRestore',
        'New-UpdateFailureBundle',
        '-ExitCode 70',
        '-ExitCode 71',
        '-ExitCode 72'
    )) { Assert-True ($content.Contains($required)) "更新控制器缺少：$required" }
    Assert-True ($content.Contains("'update', 'support-bundle'")) 'update 未纳入结构化操作日志。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
