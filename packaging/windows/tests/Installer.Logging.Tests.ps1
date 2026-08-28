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

Invoke-TestCase '结构化日志脱敏并使用受限 ACL' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-log-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $path = New-KanyikanLogFile -InstallRoot $root
        Write-KanyikanLog -Path $path -Level 'ERROR' -Command 'install' -Stage 'CONFIG' -ExitCode 40 -Message 'ADMIN_PASSWORD=top-secret Bearer abcdefghijk https://user:pwd@example.test/'
        $content = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
        Assert-True ($content.Contains('[REDACTED]')) '日志未写入标准脱敏标记。'
        foreach ($secret in @('top-secret', 'abcdefghijk', 'user:pwd')) { Assert-True (-not $content.Contains($secret)) "日志泄露 $secret。" }
        $record = $content.Trim() | ConvertFrom-Json
        Assert-True ($record.exitCode -eq 40 -and $record.stage -ceq 'CONFIG') '日志结构字段错误。'
        Assert-True (Test-KanyikanRestrictedFileAcl -Path $path) '日志 ACL 不合格。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase 'status 与 doctor 保持只读不创建日志' {
    $logRoot = Join-Path $packageRoot 'logs'
    $before = if ([IO.Directory]::Exists($logRoot)) { @([IO.Directory]::GetFiles($logRoot)).Count } else { 0 }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controllerPath status *> $null
    Assert-True ($LASTEXITCODE -eq 0) 'status 失败。'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controllerPath doctor *> $null
    Assert-True ($LASTEXITCODE -eq 0) 'doctor 失败。'
    $after = if ([IO.Directory]::Exists($logRoot)) { @([IO.Directory]::GetFiles($logRoot)).Count } else { 0 }
    Assert-True ($before -eq $after) '只读命令创建了日志。'
}

Invoke-TestCase '安装只在管理员密码通过后初始化日志' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    $password = $source.IndexOf('Read-KanyikanAdminPassword', [StringComparison]::Ordinal)
    $log = $source.IndexOf('New-KanyikanLogFile -InstallRoot', $password, [StringComparison]::Ordinal)
    $firstStateWrite = $source.IndexOf("-NextState 'PREFLIGHT_OK'", [StringComparison]::Ordinal)
    Assert-True ($password -ge 0 -and $log -gt $password) '日志在管理员密码验证前创建。'
    Assert-True ($log -lt $firstStateWrite) '安装状态写入早于日志初始化。'
}

Invoke-TestCase '终端失败输出始终说明日志位置或未创建原因' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains('未创建（只读命令或持久副作用前失败）')) '失败输出缺少无日志原因。'
    Assert-True ($source.Contains('[日志]')) '失败输出缺少日志位置。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
