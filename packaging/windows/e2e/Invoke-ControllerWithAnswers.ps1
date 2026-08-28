[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ControllerPath,
    [Parameter(Mandatory = $true)][string]$Command,
    [string]$Package,
    [string]$Backup,
    [switch]$PurgeData
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function global:Read-Host {
    param(
        [Parameter(Position = 0)][string]$Prompt,
        [switch]$AsSecureString
    )
    if ($AsSecureString) {
        $password = [Environment]::GetEnvironmentVariable('KANYIKAN_E2E_ADMIN_PASSWORD', 'Process')
        if ([string]::IsNullOrWhiteSpace($password)) { throw 'E2E 子进程缺少管理员密码。' }
        return ConvertTo-SecureString -String $password -AsPlainText -Force
    }
    if ($Prompt -match '根证书') { return 'TRUST' }
    if ($Prompt -match 'PURGE KANYIKAN DATA') { return 'PURGE KANYIKAN DATA' }
    if ($Prompt -match 'PURGE WITHOUT BACKUP') { return 'PURGE WITHOUT BACKUP' }
    if ($Prompt -match '^恢复将覆盖' -and -not [string]::IsNullOrWhiteSpace($Backup)) {
        return "RESTORE $([System.IO.Path]::GetFileName((Resolve-Path -LiteralPath $Backup)))"
    }
    throw "E2E 未声明交互回答：$Prompt"
}

function global:Start-Process {
    param([Parameter(Position = 0)]$FilePath)
    return $null
}

$arguments = @{ Command = $Command }
if (-not [string]::IsNullOrWhiteSpace($Package)) { $arguments.Package = $Package }
if (-not [string]::IsNullOrWhiteSpace($Backup)) { $arguments.Backup = $Backup }
if ($PurgeData) { $arguments.PurgeData = $true }
& $ControllerPath @arguments
