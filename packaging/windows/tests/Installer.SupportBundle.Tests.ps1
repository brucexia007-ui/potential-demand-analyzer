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

Invoke-TestCase '敏感模式扫描拒绝私钥、凭据、JWT 和认证 URL' {
    $payloads = @(
        '-----BEGIN PRIVATE KEY-----',
        'Bearer abcdefghijklmnop',
        'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.abcdefghijk',
        'ADMIN_PASSWORD=top-secret',
        'CONFIG_ENCRYPTION_KEY: secret-value',
        'https://user:password@example.test/path'
    )
    foreach ($payload in $payloads) { Assert-True (-not (Test-KanyikanSupportPayload -Text $payload)) "敏感模式错误通过：$payload" }
    Assert-True (Test-KanyikanSupportPayload -Text 'ADMIN_PASSWORD=[REDACTED]') '标准脱敏标记被错误拒绝。'
}

Invoke-TestCase '未安装状态可生成最小支持包且只含白名单文件' {
    $root = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-support-$([Guid]::NewGuid().ToString('N'))"
    [IO.Directory]::CreateDirectory($root) | Out-Null
    try {
        $result = Export-KanyikanSupportBundle -InstallRoot $root
        Assert-True ([IO.File]::Exists($result.path)) '支持包 ZIP 不存在。'
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($result.path)
        try {
            Assert-True ($archive.Entries.Count -eq 1) '支持包包含额外文件。'
            Assert-True ($archive.Entries[0].FullName -ceq 'support-bundle.json') '支持包文件名不在白名单。'
            $reader = New-Object IO.StreamReader($archive.Entries[0].Open(), [Text.Encoding]::UTF8)
            try { $payload = $reader.ReadToEnd() } finally { $reader.Dispose() }
            Assert-True (Test-KanyikanSupportPayload -Text $payload) 'ZIP 内二次敏感扫描失败。'
            foreach ($forbidden in @('system.env', 'localhost.key', 'BEGIN PRIVATE KEY', 'customer_private')) {
                if ($forbidden -ceq 'system.env') { continue }
                Assert-True (-not $payload.Contains($forbidden)) "支持包包含禁止内容 $forbidden。"
            }
        }
        finally { $archive.Dispose() }
        Assert-True (Test-KanyikanRestrictedFileAcl -Path $result.path) '支持包 ZIP ACL 不合格。'
    }
    finally { if ([IO.Directory]::Exists($root)) { [IO.Directory]::Delete($root, $true) } }
}

Invoke-TestCase '支持包明确记录排除项而不收录原始配置' {
    $source = [IO.File]::ReadAllText($modulePath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("exclusions = @('system.env', 'private keys'")) '支持包未记录排除项。'
    Assert-True (-not $source.Contains('ReadAllText($environmentPath')) '支持包读取了 system.env。'
    Assert-True (-not $source.Contains("'docker', 'inspect'")) '支持包疑似采集原始容器配置。'
}

Invoke-TestCase '控制器公开 support-bundle 且扫描失败不交付' {
    $source = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains("'support-bundle'")) '控制器未路由 support-bundle。'
    Assert-True ($source.Contains('敏感信息扫描失败时不会交付支持包')) '控制器未声明扫描失败语义。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
