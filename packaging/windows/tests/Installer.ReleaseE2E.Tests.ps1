$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$e2ePath = Join-Path $PSScriptRoot '..\e2e\Invoke-CleanOfflineInstallE2E.ps1'
$wrapperPath = Join-Path $PSScriptRoot '..\e2e\Invoke-ControllerWithAnswers.ps1'
$e2eModulePath = Join-Path $PSScriptRoot '..\e2e\Kanyikan.ReleaseE2E.psm1'
$negativeE2EPath = Join-Path $PSScriptRoot '..\e2e\Invoke-NegativeInstallE2E.ps1'
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

Invoke-TestCase 'E2E 公共执行模块支持异步中断且不泄露密码' {
    $source = [IO.File]::ReadAllText($e2eModulePath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("EnvironmentVariables['KANYIKAN_E2E_ADMIN_PASSWORD']")) '公共 E2E 模块未通过子进程环境传递密码。'
    Assert-True (-not $source.Contains('-AdminPassword')) '公共 E2E 模块将密码写入命令行。'
    Assert-True ($source.Contains('Start-KanyikanE2EController')) '公共 E2E 模块缺少异步启动。'
    Assert-True ($source.Contains('Stop-KanyikanE2EController')) '公共 E2E 模块缺少中断能力。'
    Assert-True ($source.Contains('$stdout.Contains($Handle.adminPassword)')) '公共 E2E 模块未检测终端密码泄露。'
    Assert-True ($source.Contains("KANYIKAN_CLEAN_E2E', 'Machine'")) '公共 E2E 模块未限制专用 runner。'
}

Invoke-TestCase 'E2E 公共模块只落盘通过秘密扫描且摘要一致的控制器输出' {
    Import-Module $e2eModulePath -Force
    $root = Join-Path ([IO.Path]::GetTempPath()) ("kanyikan-e2e-output-$([Guid]::NewGuid().ToString('N'))")
    try {
        $safeText = "[失败] 阶段=PREFLIGHT；退出码=22`n"
        $safeRun = [pscustomobject]@{
            stdout = $safeText.TrimEnd("`n")
            stderr = ''
            outputSha256 = Get-KanyikanE2EStringSha256 -Value ($safeText.TrimEnd("`n") + "`n")
        }
        $written = Write-KanyikanE2EControllerOutput -Run $safeRun -OutputDirectory $root -Name 'safe-install'
        Assert-True ([IO.File]::Exists($written.path)) '公共 E2E 模块未落盘安全输出。'
        Assert-True ((Get-KanyikanE2EFileSha256 -Path $written.path) -ceq $safeRun.outputSha256) '落盘输出摘要与运行记录不一致。'

        $unsafeRun = [pscustomobject]@{
            stdout = 'ADMIN_PASSWORD=top-secret-value'
            stderr = ''
            outputSha256 = Get-KanyikanE2EStringSha256 -Value "ADMIN_PASSWORD=top-secret-value`n"
        }
        $blocked = $false
        try { Write-KanyikanE2EControllerOutput -Run $unsafeRun -OutputDirectory $root -Name 'unsafe-install' | Out-Null }
        catch { $blocked = $true }
        Assert-True $blocked '公共 E2E 模块未阻断秘密输出落盘。'
        Assert-True (-not [IO.File]::Exists((Join-Path $root 'unsafe-install.log'))) '秘密输出被写入证据目录。'
    }
    finally {
        if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) }
    }
}

Invoke-TestCase '负向 E2E 固定预检与发行资产失败退出码且无持久副作用' {
    $source = [IO.File]::ReadAllText($negativeE2EPath, [Text.Encoding]::UTF8)
    [void][scriptblock]::Create($source)
    foreach ($scenario in @('PortOccupied', 'DockerStopped', 'WindowsContainers', 'DiskInsufficient', 'ManifestTampered', 'ImageArchiveCorrupt')) {
        Assert-True ($source.Contains("'$scenario'")) "负向 E2E 缺少场景：$scenario"
    }
    foreach ($mapping in @("PortOccupied = 22", "DockerStopped = 21", "WindowsContainers = 21", "DiskInsufficient = 22", "ManifestTampered = 30", "ImageArchiveCorrupt = 30")) {
        Assert-True ($source.Contains($mapping)) "负向 E2E 缺少精确退出码：$mapping"
    }
    Assert-True ($source.Contains('Assert-EarlyFailureHasNoPersistentSideEffects')) '负向 E2E 未验证失败前无持久副作用。'
    Assert-True ($source.Contains('Assert-KanyikanRuntimeAbsent')) '负向 E2E 未验证失败后没有 Compose 资源。'
    Assert-True ($source.Contains('exitHookEntered')) '负向 E2E 未用 finally 保证恢复基础设施。'
}

Invoke-TestCase '负向 E2E 覆盖三阶段中断恢复与幂等重复安装' {
    $source = [IO.File]::ReadAllText($negativeE2EPath, [Text.Encoding]::UTF8)
    foreach ($state in @('IMAGES_LOADED', 'CONFIG_CREATED', 'SERVICES_STARTING')) {
        Assert-True ($source.Contains("'$state'")) "中断恢复 E2E 缺少状态：$state"
    }
    Assert-True ($source.Contains('Stop-KanyikanE2EController')) '中断恢复 E2E 未真实终止安装进程。'
    Assert-True ($source.Contains('Wait-ForInstallState')) '中断恢复 E2E 未等待持久状态。'
    Assert-True ($source.Contains('idempotentResult')) '中断恢复 E2E 未执行第二次幂等安装。'
    Assert-True ($source.Contains('Test-KanyikanAuthenticatedSmoke')) '中断恢复 E2E 未执行认证核心 API 冒烟。'
    Assert-True ($source.Contains('configSha256BeforeIdempotent')) '中断恢复 E2E 未验证重复安装不重置配置。'
}

Invoke-TestCase '负向 E2E 证据可复核且不记录秘密或原始输出' {
    $source = [IO.File]::ReadAllText($negativeE2EPath, [Text.Encoding]::UTF8)
    foreach ($field in @('sourceCommit', 'releaseVersion', 'offlineZipSha256', 'publicKeySha256', 'windowsVersion', 'dockerVersion', 'composeVersion', 'expectedExitCode', 'actualExitCode', 'outputSha256', 'startedAt', 'completedAt')) {
        Assert-True ($source.Contains($field)) "负向 E2E 证据缺少字段：$field"
    }
    Assert-True (-not $source.Contains('adminPassword =')) '负向 E2E 证据疑似记录管理员密码。'
    Assert-True (-not $source.Contains('stdout =')) '负向 E2E 证据记录了原始标准输出。'
    Assert-True (-not $source.Contains('stderr =')) '负向 E2E 证据记录了原始标准错误。'
    Assert-True ($source.Contains('manifest.release.version')) '负向 E2E 从错误的 manifest 字段读取发行版本。'
    Assert-True (-not $source.Contains('manifest.product.version')) '负向 E2E 仍在读取不存在的 product.version。'
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
