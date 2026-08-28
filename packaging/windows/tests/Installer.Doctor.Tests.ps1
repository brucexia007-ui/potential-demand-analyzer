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

Invoke-TestCase '未安装 doctor 只读且不创建状态或探测文件' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-doctor-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $before = @([IO.Directory]::GetFileSystemEntries($root))
        $report = Get-KanyikanDoctorReport -InstallRoot $root
        $after = @([IO.Directory]::GetFileSystemEntries($root))
        Assert-True ($report.installState -ceq 'NEW') '未安装诊断状态错误。'
        Assert-True ($before.Count -eq $after.Count) 'doctor 创建了文件或目录。'
        Assert-True (-not [IO.File]::Exists((Get-KanyikanStatePath -InstallRoot $root))) 'doctor 创建了安装状态。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase 'Provider 缺失标记未配置而非安装失败' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-doctor-provider-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $report = Get-KanyikanDoctorReport -InstallRoot $root
        $provider = @($report.checks | Where-Object { $_.name -ceq 'Execution Provider' })
        Assert-True ($provider.Count -eq 1) '缺少 Provider 诊断项。'
        Assert-True ($provider[0].status -ceq '未配置或未检查') 'Provider 缺失被误报为失败。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase '诊断结果不包含代理地址或秘密值' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-doctor-redact-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $serialized = Get-KanyikanDoctorReport -InstallRoot $root | ConvertTo-Json -Depth 8
        foreach ($forbidden in @('ADMIN_PASSWORD=', 'SECRET_KEY=', 'CONFIG_ENCRYPTION_KEY=', 'BEGIN PRIVATE KEY', 'http://user:')) { Assert-True (-not $serialized.Contains($forbidden)) "诊断泄露敏感模式 $forbidden。" }
        Assert-True (-not $serialized.Contains('ProxyURL')) '诊断包含代理地址字段。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase '控制器公开 doctor 且成功退出' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("'doctor'")) '控制器未路由 doctor。'
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controllerPath doctor 2>&1
    Assert-True ($LASTEXITCODE -eq 0) 'doctor 命令未成功。'
    Assert-True ((@($output) -join "`n").Contains('Execution Provider')) 'doctor 输出缺少 Provider 状态。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
