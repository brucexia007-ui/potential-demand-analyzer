$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$packageRoot = Split-Path $PSScriptRoot -Parent
$modulePath = Join-Path $packageRoot 'lib\Kanyikan.Installer.psm1'
$controllerPath = Join-Path $packageRoot 'kanyikan.ps1'
Import-Module $modulePath -Force
$script:Passed = 0
$script:Failed = 0

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}
function New-Manifest { return [pscustomobject]@{ resources = [pscustomobject]@{ namedVolumes = [pscustomobject]@{ postgres = 'kanyikan_postgres_data'; redis = 'kanyikan_redis_data'; snapshots = 'kanyikan_snapshots_data'; skills = 'kanyikan_skills_data' } } } }

Invoke-TestCase '安装状态记录激活生命周期' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-active-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $state = New-KanyikanInstallState -InstallRoot $root
        Assert-True (-not $state.installationActive) 'NEW 错误标记为已激活。'
        foreach ($next in (Get-KanyikanInstallStates | Select-Object -Skip 1)) { $state = Set-KanyikanInstallState -State $state -NextState $next -InstallRoot $root }
        Assert-True $state.installationActive 'INSTALLED 未标记为激活。'
        $state = Set-KanyikanInstallationActive -State $state -InstallRoot $root -Active $false
        Assert-True (-not (Read-KanyikanInstallState -InstallRoot $root).installationActive) '默认卸载激活状态未持久化。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase '默认卸载计划不包含数据卷删除' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-plan-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $state = New-KanyikanInstallState -InstallRoot $root
        $plan = Get-KanyikanUninstallResourcePlan -InstallRoot $root -State $state -Manifest (New-Manifest)
        $joined = $plan.composeDownArguments -join ' '
        Assert-True ($joined.Contains('down --remove-orphans')) '默认卸载未删除项目容器和网络。'
        Assert-True (-not $joined.Contains('--volumes')) '默认卸载错误删除数据卷。'
        Assert-True ($plan.volumes.Count -eq 4) 'Purge 精确卷数量不是 4。'
        Assert-True (@($plan.volumes | Where-Object { $_ -notmatch '^kanyikan_' }).Count -eq 0) '资源计划包含非项目卷。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase '状态与 manifest 归属不一致时拒绝' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-owner-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $state = New-KanyikanInstallState -InstallRoot $root
        $manifest = New-Manifest
        $manifest.resources.namedVolumes.postgres = 'other_postgres_data'
        $threw = $false
        try { Get-KanyikanUninstallResourcePlan -InstallRoot $root -State $state -Manifest $manifest | Out-Null } catch { $threw = $_.Exception.Message.Contains('无法共同证明') }
        Assert-True $threw '归属不一致错误通过。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase 'Purge 使用固定双重确认且只列出精确资源' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains('PURGE KANYIKAN DATA')) '缺少首次固定确认文本。'
    Assert-True ($source.Contains('PURGE WITHOUT BACKUP')) '无备份时缺少第二次不同确认。'
    Assert-True ($source.Contains('最近有效备份')) 'Purge 未显示最近有效备份。'
    Assert-True (-not $source.Contains('docker volume rm kanyikan_*')) 'Purge 使用卷通配符。'
}

Invoke-TestCase '默认卸载明确保留配置、状态、备份和数据卷' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("'uninstall'")) '控制器未路由 uninstall。'
    Assert-True ($source.Contains('配置、状态、备份与数据卷仍位于')) '默认卸载未说明保留内容。'
    Assert-True ($source.Contains('-ExitCode 80')) '卸载失败未映射退出码 80。'
}

Invoke-TestCase '安全删除检查不得在识别重解析点前递归遍历' {
    $definition = (Get-Command Assert-KanyikanSafeRemovalTree).Definition
    Assert-True (-not $definition.Contains('[System.IO.SearchOption]::AllDirectories')) '删除检查会在识别 Junction 前递归进入目标。'
}

Invoke-TestCase 'Purge 必须在任何破坏动作前完成全部路径边界检查' {
    $definition = (Get-Command Invoke-KanyikanUninstall).Definition
    $boundary = $definition.IndexOf('Assert-KanyikanSafeRemovalTree', [StringComparison]::Ordinal)
    $composeDown = $definition.IndexOf('Invoke-KanyikanDockerCommand -Arguments $plan.composeDownArguments', [StringComparison]::Ordinal)
    $volumeRemove = $definition.IndexOf("@('volume', 'rm'", [StringComparison]::Ordinal)
    Assert-True ($boundary -ge 0) 'Purge 缺少路径边界检查。'
    Assert-True ($boundary -lt $composeDown) '路径边界检查晚于容器删除。'
    Assert-True ($boundary -lt $volumeRemove) '路径边界检查晚于数据卷删除。'
}

Invoke-TestCase 'Purge 删除日志目录前必须关闭当前日志写入' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    $purgeBranch = $source.IndexOf("if (`$PurgeData) {", [StringComparison]::Ordinal)
    $invoke = $source.IndexOf('Invoke-KanyikanUninstall', $purgeBranch, [StringComparison]::Ordinal)
    $disableLog = $source.IndexOf('$script:LogPath = $null', $purgeBranch, [StringComparison]::Ordinal)
    Assert-True ($purgeBranch -ge 0 -and $invoke -gt $purgeBranch) '未找到 Purge 控制分支。'
    Assert-True ($disableLog -gt $purgeBranch -and $disableLog -lt $invoke) 'Purge 未在删除 logs 前关闭当前日志写入。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
