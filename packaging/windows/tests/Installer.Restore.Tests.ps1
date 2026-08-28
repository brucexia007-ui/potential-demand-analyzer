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

Invoke-TestCase '恢复路径仅允许 data/backups 内精确目录' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-restore-$([Guid]::NewGuid().ToString('N'))"
    $valid = Join-Path $root 'data\backups\kanyikan-20260828T120000Z-a1b2c3d4'
    $outside = Join-Path $root 'outside\kanyikan-20260828T120000Z-a1b2c3d4'
    [IO.Directory]::CreateDirectory($valid) | Out-Null
    [IO.Directory]::CreateDirectory($outside) | Out-Null
    try {
        Assert-True ((Resolve-KanyikanBackupDirectory -InstallRoot $root -BackupPath $valid) -ceq [IO.Path]::GetFullPath($valid)) '合法备份路径未通过。'
        foreach ($invalid in @($outside, (Join-Path $root 'data\backups'), (Join-Path $valid '..'))) {
            $threw = $false
            try { Resolve-KanyikanBackupDirectory -InstallRoot $root -BackupPath $invalid | Out-Null } catch { $threw = $true }
            Assert-True $threw "越界或非精确路径错误通过：$invalid"
        }
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase '恢复容器离线、无依赖启动且不传递秘密' {
    $arguments = @(Get-KanyikanRestoreArguments -InstallRoot 'C:\Kanyikan' -BackupName 'kanyikan-20260828T120000Z-a1b2c3d4')
    $joined = $arguments -join ' '
    Assert-True ($joined.Contains('run --rm --no-deps --pull never --entrypoint python backend')) '恢复容器启动边界错误。'
    Assert-True ($joined.Contains('app.tools.local_backup restore')) '未调用完整恢复工具。'
    foreach ($secret in @('DATABASE_URL', 'POSTGRES_PASSWORD', 'SECRET_KEY', 'CONFIG_ENCRYPTION_KEY')) { Assert-True (-not $joined.Contains($secret)) "恢复命令行包含秘密字段 $secret。" }
}

Invoke-TestCase '控制器严格按验证、确认、保护备份、停服、恢复排序' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    $positions = @(
        $source.IndexOf('Invoke-KanyikanValidateBackup', [StringComparison]::Ordinal),
        $source.IndexOf('恢复将覆盖当前数据库', [StringComparison]::Ordinal),
        $source.IndexOf('RESTORE_PROTECTION_BACKUP', [StringComparison]::Ordinal),
        $source.IndexOf('Stop-KanyikanServices', $source.IndexOf("'restore'"), [StringComparison]::Ordinal),
        $source.IndexOf('Invoke-KanyikanRestore', [StringComparison]::Ordinal)
    )
    for ($index = 0; $index -lt $positions.Count; $index++) { Assert-True ($positions[$index] -ge 0) "缺少恢复步骤 $index。"; if ($index -gt 0) { Assert-True ($positions[$index] -gt $positions[$index - 1]) "恢复步骤 $index 顺序错误。" } }
}

Invoke-TestCase '恢复失败映射 61、停止入口并保留保护备份' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains('-ExitCode 61')) '恢复失败未映射退出码 61。'
    Assert-True ($source.Contains('入口已停止；恢复前保护备份保留在')) '恢复失败未说明安全停止和保护备份。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
