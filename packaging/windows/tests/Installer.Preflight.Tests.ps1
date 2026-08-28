$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$modulePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\Kanyikan.Installer.psm1'
Import-Module $modulePath -Force

$script:Passed = 0
$script:Failed = 0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body
        $script:Passed++
        Write-Host "PASS $Name"
    }
    catch {
        $script:Failed++
        Write-Host "FAIL $Name - $($_.Exception.Message)"
    }
}

function New-ValidFacts {
    return [pscustomobject]@{
        isWindows = $true
        windowsMajorVersion = 10
        architecture = 'AMD64'
        powerShellEdition = 'Desktop'
        powerShellVersion = [Version]'5.1'
        dockerDesktopInstalled = $true
        dockerCliAvailable = $true
        dockerEngineAvailable = $true
        dockerOsType = 'linux'
        composeMajorVersion = 2
        cpuCores = 4
        memoryBytes = 8589934592L
        freeDiskBytes = 21474836480L
        portAvailable = $true
        installRootWritable = $true
        dockerProxyEnabled = $false
    }
}

Invoke-TestCase '合法 Windows x64 环境通过' {
    $result = Test-KanyikanPreflightFacts -Facts (New-ValidFacts)
    Assert-True $result.passed '合法环境未通过。'
    Assert-True ($result.exitCode -eq 0) '合法环境退出码错误。'
    Assert-True ($result.checks.Count -eq 12) '预检项数量不完整。'
}

$platformFailures = @(
    @{ property = 'isWindows'; value = $false; check = 'Windows 10/11 x64' },
    @{ property = 'architecture'; value = 'ARM64'; check = 'Windows 10/11 x64' },
    @{ property = 'powerShellEdition'; value = 'Core'; check = 'Windows PowerShell 5.1+' },
    @{ property = 'powerShellVersion'; value = [Version]'5.0'; check = 'Windows PowerShell 5.1+' }
)
foreach ($case in $platformFailures) {
    Invoke-TestCase "平台拒绝 $($case.property)" {
        $facts = New-ValidFacts
        $facts.($case.property) = $case.value
        $result = Test-KanyikanPreflightFacts -Facts $facts
        Assert-True (-not $result.passed) '不支持的平台错误通过。'
        Assert-True ($result.exitCode -eq 20) '平台失败退出码不是 20。'
        Assert-True ($result.failedCheck -ceq $case.check) '平台失败项不准确。'
    }
}

$dockerFailures = @(
    @{ property = 'dockerDesktopInstalled'; value = $false; check = 'Docker Desktop' },
    @{ property = 'dockerCliAvailable'; value = $false; check = 'Docker CLI' },
    @{ property = 'dockerEngineAvailable'; value = $false; check = 'Docker Engine' },
    @{ property = 'dockerOsType'; value = 'windows'; check = 'Linux Containers' },
    @{ property = 'composeMajorVersion'; value = 1; check = 'Docker Compose v2' }
)
foreach ($case in $dockerFailures) {
    Invoke-TestCase "Docker 拒绝 $($case.property)" {
        $facts = New-ValidFacts
        $facts.($case.property) = $case.value
        $result = Test-KanyikanPreflightFacts -Facts $facts
        Assert-True (-not $result.passed) '不合格的 Docker 环境错误通过。'
        Assert-True ($result.exitCode -eq 21) 'Docker 失败退出码不是 21。'
        Assert-True ($result.failedCheck -ceq $case.check) 'Docker 失败项不准确。'
    }
}

$resourceFailures = @(
    @{ property = 'cpuCores'; value = 3; check = 'CPU >= 4' },
    @{ property = 'memoryBytes'; value = 8589934591L; check = 'Memory >= 8 GiB' },
    @{ property = 'freeDiskBytes'; value = 21474836479L; check = 'Disk >= 20 GiB' },
    @{ property = 'portAvailable'; value = $false; check = '127.0.0.1:10443 available' },
    @{ property = 'installRootWritable'; value = $false; check = 'Install root writable' }
)
foreach ($case in $resourceFailures) {
    Invoke-TestCase "资源拒绝 $($case.property)" {
        $facts = New-ValidFacts
        $facts.($case.property) = $case.value
        $result = Test-KanyikanPreflightFacts -Facts $facts
        Assert-True (-not $result.passed) '资源不足错误通过。'
        Assert-True ($result.exitCode -eq 22) '资源失败退出码不是 22。'
        Assert-True ($result.failedCheck -ceq $case.check) '资源失败项不准确。'
    }
}

Invoke-TestCase '代理只暴露启用状态' {
    $facts = New-ValidFacts
    $facts.dockerProxyEnabled = $true
    $result = Test-KanyikanPreflightFacts -Facts $facts
    Assert-True $result.proxyEnabled '未报告代理启用状态。'
    $serialized = $result | ConvertTo-Json -Depth 6
    Assert-True (-not $serialized.Contains('http://')) '预检结果泄露代理地址。'
    Assert-True (-not $serialized.Contains('@')) '预检结果疑似泄露代理凭据。'
}

Invoke-TestCase '仅显式确认项目自有入口时允许端口占用' {
    $facts = New-ValidFacts
    $facts.portAvailable = $false
    Assert-True (-not (Test-KanyikanPreflightFacts -Facts $facts).passed) '普通端口占用错误通过。'
    $result = Test-KanyikanPreflightFacts -Facts $facts -AllowOwnedEntrypoint
    Assert-True $result.passed '显式确认的项目自有入口未通过。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
