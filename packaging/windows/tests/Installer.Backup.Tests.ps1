$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$modulePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\Kanyikan.Installer.psm1'
$controllerPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'kanyikan.ps1'
Import-Module $modulePath -Force
$script:Passed = 0
$script:Failed = 0

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}
function New-State {
    return [pscustomobject]@{ productVersion = '1.0.0'; manifestSha256 = 'a' * 64; releasePublicKeySha256 = 'b' * 64 }
}

Invoke-TestCase '备份命令使用固定项目 Backend 且不传递秘密' {
    $root = Join-Path ([IO.Path]::GetTempPath()) '看一看 备份'
    $arguments = @(Get-KanyikanBackupArguments -InstallRoot $root -State (New-State))
    $joined = $arguments -join ' '
    Assert-True ($joined.Contains('exec --no-TTY backend python -m app.tools.local_backup create')) '未在固定 Backend 容器执行完整备份。'
    foreach ($secret in @('DATABASE_URL', 'POSTGRES_PASSWORD', 'SECRET_KEY', 'CONFIG_ENCRYPTION_KEY', 'ADMIN_PASSWORD')) { Assert-True (-not $joined.Contains($secret)) "备份命令行包含秘密字段 $secret。" }
    Assert-True ($joined.Contains('--snapshots-root /app/data/snapshots')) '未备份快照卷。'
    Assert-True ($joined.Contains('--skills-root /app/data/workspace_skills')) '未备份 Skill 卷。'
}

Invoke-TestCase '最终复核只接受精确备份目录名' {
    $arguments = @(Get-KanyikanBackupArguments -InstallRoot 'C:\Kanyikan' -State (New-State) -BackupName 'kanyikan-20260828T120000Z-a1b2c3d4')
    Assert-True (($arguments -join ' ').Contains('validate --backup-root /backups --backup /backups/kanyikan-20260828T120000Z-a1b2c3d4')) '最终复核参数错误。'
    foreach ($invalid in @('../outside', 'kanyikan-*', 'C:\outside', 'kanyikan-20260828')) {
        $threw = $false
        try { Get-KanyikanBackupArguments -InstallRoot 'C:\Kanyikan' -State (New-State) -BackupName $invalid | Out-Null } catch { $threw = $true }
        Assert-True $threw "非法备份名错误通过：$invalid"
    }
}

Invoke-TestCase '控制器失败映射退出 60 并提示单独保护 system.env' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("'backup'")) '控制器未路由 backup。'
    Assert-True ($source.Contains('-ExitCode 60')) '备份失败未映射退出码 60。'
    Assert-True ($source.Contains('另行安全保存 config/system.env')) '备份成功未提示保护 system.env。'
}

Invoke-TestCase '备份目录和文件 ACL 仅允许当前用户与 Administrators' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-backup-acl-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory((Join-Path $root 'nested')) | Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'nested\artifact'), 'fixture')
    try {
        Set-KanyikanBackupAcl -BackupDirectory $root
        $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $administrators = (New-Object Security.Principal.SecurityIdentifier([Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)).Value
        $security = [IO.Directory]::GetAccessControl($root)
        $rules = @($security.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))
        Assert-True $security.AreAccessRulesProtected '备份目录仍继承外部 ACL。'
        Assert-True ($rules.Count -eq 2) '备份目录 ACL 规则数量不是 2。'
        foreach ($rule in $rules) { Assert-True (@($currentUser, $administrators) -ccontains $rule.IdentityReference.Value) '备份目录包含未授权主体。' }
        Assert-True (Test-KanyikanRestrictedFileAcl -Path (Join-Path $root 'nested\artifact')) '备份文件 ACL 不合格。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
