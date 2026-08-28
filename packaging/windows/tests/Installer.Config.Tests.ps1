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
function ConvertTo-Secure([string]$Value) { return ConvertTo-SecureString $Value -AsPlainText -Force }
function New-Manifest {
    $images = [ordered]@{}
    foreach ($name in @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless')) {
        $images[$name] = [pscustomobject]@{ reference = "registry.local/kanyikan/$name@sha256:" + ('a' * 64) }
    }
    return [pscustomobject]@{ images = [pscustomobject]$images }
}
function Decode-EnvValue([string]$Value) {
    Assert-True ($Value.StartsWith('"') -and $Value.EndsWith('"')) '环境值没有双引号编码。'
    $body = $Value.Substring(1, $Value.Length - 2)
    $builder = New-Object Text.StringBuilder
    for ($index = 0; $index -lt $body.Length; $index++) {
        if ($body[$index] -eq '$' -and $index + 1 -lt $body.Length -and $body[$index + 1] -eq '$') { [void]$builder.Append('$'); $index++; continue }
        if ($body[$index] -eq '\' -and $index + 1 -lt $body.Length) { [void]$builder.Append($body[$index + 1]); $index++; continue }
        [void]$builder.Append($body[$index])
    }
    return $builder.ToString()
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-config-$([Guid]::NewGuid().ToString('N'))"
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
try {
    Invoke-TestCase '管理员密码策略与两次确认一致' {
        $valid = Test-KanyikanAdminPassword -Password (ConvertTo-Secure 'Long password 123!') -Confirmation (ConvertTo-Secure 'Long password 123!')
        Assert-True $valid.passed '合法密码未通过。'
        $short = Test-KanyikanAdminPassword -Password (ConvertTo-Secure 'short') -Confirmation (ConvertTo-Secure 'short')
        Assert-True (-not $short.passed) '短密码错误通过。'
        $mismatch = Test-KanyikanAdminPassword -Password (ConvertTo-Secure 'Long password 123!') -Confirmation (ConvertTo-Secure 'Long password 456!')
        Assert-True (-not $mismatch.passed) '两次不一致错误通过。'
    }

    Invoke-TestCase '特殊字符密码可逆且 URL 凭据有效编码' {
        $destination = Join-Path $testRoot 'special\system.env'
        $passwordText = 'A long p@ss: word#%$\"!'
        Write-KanyikanSystemEnvironment -TemplatePath (Join-Path (Split-Path $PSScriptRoot -Parent) 'system.env.template') -DestinationPath $destination -Manifest (New-Manifest) -AdminPassword (ConvertTo-Secure $passwordText)
        $lines = @{}
        foreach ($line in [IO.File]::ReadAllLines($destination, [Text.Encoding]::UTF8)) { if ($line -match '^([A-Z_]+)=(.*)$') { $lines[$Matches[1]] = $Matches[2] } }
        Assert-True ((Decode-EnvValue $lines.ADMIN_PASSWORD) -ceq $passwordText) '管理员密码编码不可逆。'
        $databaseUrl = Decode-EnvValue $lines.DATABASE_URL
        $redisUrl = Decode-EnvValue $lines.REDIS_URL
        Assert-True ($databaseUrl -match '^postgresql://demand_user:[A-Za-z0-9_-]+@postgres:5432/demand_analyzer$') '数据库 URL 未正确编码。'
        Assert-True ($redisUrl -match '^redis://:[A-Za-z0-9_-]+@redis:6379/0$') 'Redis URL 未正确编码。'
    }

    Invoke-TestCase '独立安装生成不同且合格的随机密钥' {
        $manifest = New-Manifest
        $first = Join-Path $testRoot 'first\system.env'
        $second = Join-Path $testRoot 'second\system.env'
        $password = ConvertTo-Secure 'Long password 123!'
        $template = Join-Path (Split-Path $PSScriptRoot -Parent) 'system.env.template'
        Write-KanyikanSystemEnvironment -TemplatePath $template -DestinationPath $first -Manifest $manifest -AdminPassword $password
        Write-KanyikanSystemEnvironment -TemplatePath $template -DestinationPath $second -Manifest $manifest -AdminPassword $password
        $read = {
            param($path)
            $result = @{}
            foreach ($line in [IO.File]::ReadAllLines($path, [Text.Encoding]::UTF8)) {
                if ($line -match '^([A-Z_]+)=(.*)$') {
                    $result[$Matches[1]] = if ($Matches[2].StartsWith('"')) { Decode-EnvValue $Matches[2] } else { $Matches[2] }
                }
            }
            return $result
        }
        $one = & $read $first
        $two = & $read $second
        foreach ($key in @('SECRET_KEY', 'CONFIG_ENCRYPTION_KEY', 'POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'BROWSERLESS_TOKEN')) {
            Assert-True ($one[$key] -cne $two[$key]) "$key 在两次安装中重复。"
        }
        Assert-True ($one.SECRET_KEY.Length -ge 48) 'SECRET_KEY 长度不足。'
        $fernetRaw = $one.CONFIG_ENCRYPTION_KEY.Replace('-', '+').Replace('_', '/')
        Assert-True ([Convert]::FromBase64String($fernetRaw).Length -eq 32) 'Fernet key 不是 32 字节。'
    }

    Invoke-TestCase 'system.env ACL 仅允许当前用户和 Administrators' {
        $path = Join-Path $testRoot 'acl\system.env'
        Write-KanyikanSystemEnvironment -TemplatePath (Join-Path (Split-Path $PSScriptRoot -Parent) 'system.env.template') -DestinationPath $path -Manifest (New-Manifest) -AdminPassword (ConvertTo-Secure 'Long password 123!')
        Assert-True (Test-KanyikanRestrictedFileAcl -Path $path) 'system.env ACL 过宽。'
        Assert-True (Test-KanyikanSystemEnvironment -Path $path -Manifest (New-Manifest)) '已生成配置复核失败。'
    }

    Invoke-TestCase '拒绝覆盖现有 system.env' {
        $path = Join-Path $testRoot 'existing\system.env'
        Write-KanyikanSystemEnvironment -TemplatePath (Join-Path (Split-Path $PSScriptRoot -Parent) 'system.env.template') -DestinationPath $path -Manifest (New-Manifest) -AdminPassword (ConvertTo-Secure 'Long password 123!')
        $before = [IO.File]::ReadAllBytes($path)
        $threw = $false
        try { Write-KanyikanSystemEnvironment -TemplatePath (Join-Path (Split-Path $PSScriptRoot -Parent) 'system.env.template') -DestinationPath $path -Manifest (New-Manifest) -AdminPassword (ConvertTo-Secure 'Another password 456!') } catch { $threw = $_.Exception.Message.Contains('拒绝覆盖') }
        Assert-True $threw '现有配置被允许覆盖。'
        Assert-True ([Convert]::ToBase64String($before) -ceq [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))) '现有配置内容被改变。'
    }
}
finally {
    if ([IO.Directory]::Exists($testRoot)) { [IO.Directory]::Delete($testRoot, $true) }
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
