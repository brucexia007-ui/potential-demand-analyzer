$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$e2ePath = Join-Path $PSScriptRoot '..\e2e\Invoke-CleanOfflineInstallE2E.ps1'
$wrapperPath = Join-Path $PSScriptRoot '..\e2e\Invoke-ControllerWithAnswers.ps1'
$script:Passed = 0
$script:Failed = 0

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}

Invoke-TestCase 'E2E 仅允许专用管理员 runner 且强制真实断网守卫' {
    $source = [IO.File]::ReadAllText($e2ePath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("KANYIKAN_CLEAN_E2E', 'Machine'")) 'E2E 未要求机器级专用 runner 标记。'
    Assert-True ($source.Contains('WindowsBuiltInRole]::Administrator')) 'E2E 未要求管理员权限。'
    $removeImages = $source.IndexOf('docker image rm --force', [StringComparison]::Ordinal)
    $enterOffline = $source.IndexOf('Invoke-Guard -Path $enterOfflinePath', [StringComparison]::Ordinal)
    $publicProbe = $source.IndexOf("https://example.com/", [StringComparison]::Ordinal)
    $install = $source.IndexOf("Invoke-Controller -Command 'install'", [StringComparison]::Ordinal)
    Assert-True ($removeImages -ge 0 -and $removeImages -lt $enterOffline -and $enterOffline -lt $publicProbe -and $publicProbe -lt $install) 'E2E 未按清缓存、断网、验证断网、安装的顺序执行。'
    Assert-True ($source.Contains('Invoke-Guard -Path $exitOfflinePath')) 'E2E 未在 finally 恢复 runner 网络。'
}

Invoke-TestCase 'E2E 管理员密码不进入命令行、输出或证据' {
    $source = [IO.File]::ReadAllText($e2ePath, [Text.Encoding]::UTF8)
    $wrapper = [IO.File]::ReadAllText($wrapperPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("EnvironmentVariables['KANYIKAN_E2E_ADMIN_PASSWORD']")) 'E2E 未通过子进程环境传递临时密码。'
    Assert-True (-not $source.Contains('-AdminPassword')) 'E2E 将管理员密码作为命令行参数。'
    Assert-True ($source.Contains('$stdout.Contains($script:AdminPassword)')) 'E2E 未阻断终端密码泄露。'
    Assert-True ($wrapper.Contains("GetEnvironmentVariable('KANYIKAN_E2E_ADMIN_PASSWORD', 'Process')")) '交互包装器未从子进程环境读取密码。'
    Assert-True (-not $source.Contains('adminPassword =')) 'E2E 证据疑似记录管理员密码。'
}

Invoke-TestCase 'E2E 覆盖核心安装生命周期和可复核证据' {
    $source = [IO.File]::ReadAllText($e2ePath, [Text.Encoding]::UTF8)
    $lastIndex = -1
    foreach ($step in @(
        "Invoke-Controller -Command 'install'",
        'Test-KanyikanAuthenticatedSmoke',
        "Invoke-Controller -Command 'restart'",
        "Invoke-Controller -Command 'backup'",
        "Invoke-Controller -Command 'uninstall' | Out-Null",
        "Invoke-Controller -Command 'start'",
        "Invoke-Controller -Command 'uninstall' -PurgeData"
    )) {
        $index = $source.IndexOf($step, $lastIndex + 1, [StringComparison]::Ordinal)
        Assert-True ($index -gt $lastIndex) "E2E 缺少或乱序执行：$step"
        $lastIndex = $index
    }
    foreach ($field in @('sourceCommit', 'releaseVersion', 'offlineZipSha256', 'manifestSha256', 'controllerRuns', 'checks')) {
        Assert-True ($source.Contains($field)) "E2E 证据缺少字段：$field"
    }
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
