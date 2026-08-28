$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$modulePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\Kanyikan.Installer.psm1'
Import-Module $modulePath -Force
$script:Passed = 0
$script:Failed = 0

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}
function New-ServiceFacts {
    $facts = @()
    foreach ($service in @('postgres', 'redis', 'backend', 'worker', 'crawler', 'beat', 'outbox-relay', 'frontend', 'nginx', 'browserless')) {
        $publishers = if ($service -ceq 'nginx') { @([pscustomobject]@{ URL = '127.0.0.1'; PublishedPort = 10443; TargetPort = 443; Protocol = 'tcp' }) } else { @() }
        $facts += [pscustomobject]@{ Service = $service; State = 'running'; Health = 'healthy'; Publishers = $publishers }
    }
    return $facts
}

Invoke-TestCase 'Compose 参数固定项目、环境文件和发行配置' {
    $root = Join-Path ([IO.Path]::GetTempPath()) '看一看 安装目录'
    $arguments = @(Get-KanyikanComposeArguments -InstallRoot $root -Arguments @('up', '--detach', '--pull', 'never'))
    Assert-True ($arguments[0] -ceq 'compose') '未使用 docker compose。'
    Assert-True (@($arguments | Where-Object { $_ -ceq 'kanyikan' }).Count -eq 1) 'Compose project name 不固定。'
    Assert-True (@($arguments | Where-Object { $_ -ceq (Join-Path $root 'config\system.env') }).Count -eq 1) 'system.env 路径未作为单个参数保留。'
    Assert-True (@($arguments | Where-Object { $_ -ceq (Join-Path $root 'compose.release.yml') }).Count -eq 1) '发行 Compose 路径未作为单个参数保留。'
    Assert-True (($arguments -join ' ').Contains('--pull never')) '启动参数未禁止拉取。'
}

Invoke-TestCase '十个服务健康且只有 Nginx 发布 loopback 端口时通过' {
    $result = Test-KanyikanServiceFacts -Facts @(New-ServiceFacts)
    Assert-True $result.passed '合法服务拓扑未通过。'
}

Invoke-TestCase '服务缺失或增加时拒绝' {
    $missing = @(New-ServiceFacts | Select-Object -First 9)
    Assert-True (-not (Test-KanyikanServiceFacts -Facts $missing).passed) '缺少服务错误通过。'
    $extra = @(New-ServiceFacts) + @([pscustomobject]@{ Service = 'extra'; State = 'running'; Health = 'healthy'; Publishers = @() })
    Assert-True (-not (Test-KanyikanServiceFacts -Facts $extra).passed) '额外服务错误通过。'
}

Invoke-TestCase '任一服务不健康时拒绝' {
    $facts = @(New-ServiceFacts)
    $facts[2].Health = 'unhealthy'
    $result = Test-KanyikanServiceFacts -Facts $facts
    Assert-True (-not $result.passed -and $result.reason.Contains('backend')) '不健康服务未准确拒绝。'
}

Invoke-TestCase '非 Nginx 服务发布端口时拒绝' {
    $facts = @(New-ServiceFacts)
    $facts[0].Publishers = @([pscustomobject]@{ URL = '0.0.0.0'; PublishedPort = 5432; TargetPort = 5432; Protocol = 'tcp' })
    $result = Test-KanyikanServiceFacts -Facts $facts
    Assert-True (-not $result.passed -and $result.reason.Contains('postgres')) '数据库宿主端口未被拒绝。'
}

Invoke-TestCase 'Nginx 非 loopback 或错误端口时拒绝' {
    $facts = @(New-ServiceFacts)
    ($facts | Where-Object { $_.Service -ceq 'nginx' }).Publishers[0].URL = '0.0.0.0'
    Assert-True (-not (Test-KanyikanServiceFacts -Facts $facts).passed) '公网监听错误通过。'
    $facts = @(New-ServiceFacts)
    ($facts | Where-Object { $_.Service -ceq 'nginx' }).Publishers[0].PublishedPort = 443
    Assert-True (-not (Test-KanyikanServiceFacts -Facts $facts).passed) '错误入口端口错误通过。'
}

Invoke-TestCase '健康等待有明确总超时和轮询范围' {
    $command = Get-Command Wait-KanyikanBootstrapReady
    $timeoutRange = $command.Parameters.TimeoutSeconds.Attributes | Where-Object { $_ -is [Management.Automation.ValidateRangeAttribute] }
    $pollRange = $command.Parameters.PollIntervalSeconds.Attributes | Where-Object { $_ -is [Management.Automation.ValidateRangeAttribute] }
    Assert-True ($timeoutRange.MinRange -eq 1) '健康总超时缺少下界。'
    Assert-True ($timeoutRange.MaxRange -eq 3600) '健康总超时缺少上界。'
    Assert-True ($pollRange.MaxRange -eq 60) '轮询间隔缺少上界。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
