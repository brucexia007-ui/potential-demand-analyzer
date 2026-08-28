$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$modulePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\Kanyikan.Installer.psm1'
Import-Module $modulePath -Force

if (-not ('KanyikanTestSigning' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;

public static class KanyikanTestSigning
{
    private static byte[] Length(int value)
    {
        if (value < 128) return new byte[] { (byte)value };
        byte[] raw = BitConverter.GetBytes(value);
        Array.Reverse(raw);
        int start = 0;
        while (raw[start] == 0) start++;
        byte[] result = new byte[raw.Length - start + 1];
        result[0] = (byte)(0x80 | (raw.Length - start));
        Buffer.BlockCopy(raw, start, result, 1, raw.Length - start);
        return result;
    }

    private static byte[] Integer(byte[] value)
    {
        bool prefix = (value[0] & 0x80) != 0;
        byte[] length = Length(value.Length + (prefix ? 1 : 0));
        byte[] result = new byte[1 + length.Length + value.Length + (prefix ? 1 : 0)];
        result[0] = 0x02;
        Buffer.BlockCopy(length, 0, result, 1, length.Length);
        Buffer.BlockCopy(value, 0, result, 1 + length.Length + (prefix ? 1 : 0), value.Length);
        return result;
    }

    private static byte[] Sequence(byte[] first, byte[] second)
    {
        byte[] length = Length(first.Length + second.Length);
        byte[] result = new byte[1 + length.Length + first.Length + second.Length];
        result[0] = 0x30;
        Buffer.BlockCopy(length, 0, result, 1, length.Length);
        Buffer.BlockCopy(first, 0, result, 1 + length.Length, first.Length);
        Buffer.BlockCopy(second, 0, result, 1 + length.Length + first.Length, second.Length);
        return result;
    }

    public static byte[] NewPrivateKey()
    {
        using (RSACryptoServiceProvider rsa = new RSACryptoServiceProvider(2048))
        {
            rsa.PersistKeyInCsp = false;
            return rsa.ExportCspBlob(true);
        }
    }

    public static byte[] PublicKeyPem(byte[] privateKey)
    {
        using (RSACryptoServiceProvider rsa = new RSACryptoServiceProvider())
        {
            rsa.PersistKeyInCsp = false;
            rsa.ImportCspBlob(privateKey);
            RSAParameters p = rsa.ExportParameters(false);
            string body = Convert.ToBase64String(Sequence(Integer(p.Modulus), Integer(p.Exponent)), Base64FormattingOptions.InsertLineBreaks);
            return Encoding.ASCII.GetBytes("-----BEGIN RSA PUBLIC KEY-----\n" + body.Replace("\r\n", "\n") + "\n-----END RSA PUBLIC KEY-----\n");
        }
    }

    public static byte[] Sign(byte[] privateKey, byte[] content)
    {
        using (RSACryptoServiceProvider rsa = new RSACryptoServiceProvider())
        {
            rsa.PersistKeyInCsp = false;
            rsa.ImportCspBlob(privateKey);
            return rsa.SignData(content, CryptoConfig.MapNameToOID("SHA256"));
        }
    }
}
'@
}

$script:Passed = 0
$script:Failed = 0

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}

function Get-Hash {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose(); $stream.Dispose() }
}

function New-TestPackage {
    param([string]$Root)
    [IO.Directory]::CreateDirectory($Root) | Out-Null
    $privateKey = [KanyikanTestSigning]::NewPrivateKey()
    $files = [ordered]@{
        'install.cmd' = 'entrypoint'; 'kanyikan.ps1' = 'controller'; 'lib/Kanyikan.Installer.psm1' = 'module';
        'compose.release.yml' = 'compose'; 'images/kanyikan-images-windows-amd64.tar' = 'image_archive';
        'config/system.env.template' = 'config_template'; 'docs/快速安装说明.md' = 'documentation';
        'docs/故障排查.md' = 'documentation'; 'docs/第三方许可证.html' = 'documentation';
        'public-key.pem' = 'public_key'; 'VERSION' = 'version'; 'LICENSE' = 'license'
    }
    foreach ($relativePath in $files.Keys) {
        $fullPath = Join-Path $Root $relativePath
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($fullPath)) | Out-Null
        $content = if ($relativePath -ceq 'VERSION') { [Text.Encoding]::UTF8.GetBytes('1.0.0') } else { [Text.Encoding]::UTF8.GetBytes("fixture:$relativePath") }
        [IO.File]::WriteAllBytes($fullPath, $content)
    }
    [IO.File]::WriteAllBytes((Join-Path $Root 'public-key.pem'), [KanyikanTestSigning]::PublicKeyPem($privateKey))

    $fileEntries = @()
    foreach ($relativePath in $files.Keys) {
        $fullPath = Join-Path $Root $relativePath
        $fileEntries += [ordered]@{ path = $relativePath; role = $files[$relativePath]; sizeBytes = (Get-Item $fullPath).Length; sha256 = Get-Hash $fullPath }
    }
    $publicKeyHash = Get-Hash (Join-Path $Root 'public-key.pem')
    $digest = 'sha256:' + ('a' * 64)
    $imageId = 'sha256:' + ('b' * 64)
    function New-Image([string[]]$services) {
        return [ordered]@{ reference = "registry.local/kanyikan/image@$digest"; digest = $digest; imageId = $imageId; platform = 'linux/amd64'; archivePath = 'images/kanyikan-images-windows-amd64.tar'; composeServices = $services }
    }
    $manifest = [ordered]@{
        schemaVersion = 1; product = 'Kanyikan'
        release = [ordered]@{ version = '1.0.0'; publishedAt = '2026-08-28T00:00:00Z'; sourceCommit = ('c' * 40); packageType = 'offline' }
        target = [ordered]@{ os = 'windows'; architecture = 'amd64'; dockerPlatform = 'linux/amd64'; deploymentProfile = 'local_appliance' }
        requirements = [ordered]@{ windowsEditions = @('10', '11'); powershellMinimumVersion = '5.1'; dockerDesktopRequired = $true; composeMajorVersion = 2; minimumCpuCores = 4; minimumMemoryBytes = 8589934592L; minimumFreeDiskBytes = 21474836480L }
        entrypoint = [ordered]@{ scheme = 'https'; host = '127.0.0.1'; port = 10443; path = '/' }
        tls = [ordered]@{ leafValidityDays = 365; caValidityDays = 1825 }
        compose = [ordered]@{ path = 'compose.release.yml'; projectName = 'kanyikan'; pullPolicy = 'never'; services = @('postgres', 'redis', 'backend', 'worker', 'crawler', 'beat', 'outbox-relay', 'frontend', 'nginx', 'browserless') }
        resources = [ordered]@{ namedVolumes = [ordered]@{ postgres = 'kanyikan_postgres_data'; redis = 'kanyikan_redis_data'; snapshots = 'kanyikan_snapshots_data'; skills = 'kanyikan_skills_data' } }
        files = $fileEntries
        images = [ordered]@{ backend = New-Image @('backend', 'worker', 'crawler', 'beat', 'outbox-relay'); frontend = New-Image @('frontend'); postgres = New-Image @('postgres'); redis = New-Image @('redis'); nginx = New-Image @('nginx'); browserless = New-Image @('browserless') }
        upgrade = [ordered]@{ supportedFrom = @(); migration = [ordered]@{ strategy = 'none'; requiresFullBackup = $true; rollbackStrategy = 'restore_full_backup' }; smokeTests = @('https_health', 'https_ready', 'admin_login', 'core_api') }
        signing = [ordered]@{ algorithm = 'RSASSA-PKCS1-v1_5-SHA256'; keyId = 'test-key'; publicKeySha256 = $publicKeyHash; publicKeyPath = 'public-key.pem'; signaturePath = 'release-manifest.sig' }
    }
    $utf8 = New-Object Text.UTF8Encoding($false)
    $manifestPath = Join-Path $Root 'release-manifest.json'
    [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 12), $utf8)
    $manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
    [IO.File]::WriteAllBytes((Join-Path $Root 'release-manifest.sig'), [KanyikanTestSigning]::Sign($privateKey, $manifestBytes))
    $checksumLines = @($fileEntries | ForEach-Object { "$($_.sha256)  $($_.path)" })
    [IO.File]::WriteAllLines((Join-Path $Root 'manifest.sha256'), $checksumLines, $utf8)
    return [pscustomobject]@{ root = $Root; trustedHash = $publicKeyHash; privateKey = $privateKey; manifest = $manifest }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-artifact-$([Guid]::NewGuid().ToString('N'))"
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
try {
    Invoke-TestCase '合法签名发行包通过' {
        $package = New-TestPackage (Join-Path $testRoot 'valid')
        $result = Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 $package.trustedHash
        Assert-True $result.passed '合法发行包未通过。'
        Assert-True ($result.version -ceq '1.0.0') '版本结果错误。'
    }

    Invoke-TestCase '固定信任锚不匹配时拒绝' {
        $package = New-TestPackage (Join-Path $testRoot 'wrong-trust')
        $threw = $false
        try { Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 ('0' * 64) | Out-Null } catch { $threw = $_.Exception.Message.Contains('信任锚') }
        Assert-True $threw '错误公钥信任锚未被拒绝。'
    }

    Invoke-TestCase '签名被替换时拒绝' {
        $package = New-TestPackage (Join-Path $testRoot 'bad-signature')
        [IO.File]::WriteAllBytes((Join-Path $package.root 'release-manifest.sig'), [byte[]](1..32))
        $threw = $false
        try { Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 $package.trustedHash | Out-Null } catch { $threw = $_.Exception.Message.Contains('签名无效') }
        Assert-True $threw '被替换签名未被拒绝。'
    }

    Invoke-TestCase '静态文件被修改时拒绝' {
        $package = New-TestPackage (Join-Path $testRoot 'tampered-file')
        [IO.File]::AppendAllText((Join-Path $package.root 'LICENSE'), 'tampered')
        $threw = $false
        try { Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 $package.trustedHash | Out-Null } catch { $threw = $_.Exception.Message.Contains('SHA256') }
        Assert-True $threw '被修改文件未被拒绝。'
    }

    Invoke-TestCase '危险相对路径在签名有效时仍拒绝' {
        $package = New-TestPackage (Join-Path $testRoot 'traversal')
        $package.manifest.files += [ordered]@{ path = '../outside.ps1'; role = 'documentation'; sizeBytes = 1; sha256 = ('d' * 64) }
        $utf8 = New-Object Text.UTF8Encoding($false)
        $manifestPath = Join-Path $package.root 'release-manifest.json'
        [IO.File]::WriteAllText($manifestPath, ($package.manifest | ConvertTo-Json -Depth 12), $utf8)
        [IO.File]::WriteAllBytes((Join-Path $package.root 'release-manifest.sig'), [KanyikanTestSigning]::Sign($package.privateKey, [IO.File]::ReadAllBytes($manifestPath)))
        $threw = $false
        try { Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 $package.trustedHash | Out-Null } catch { $threw = $_.Exception.Message.Contains('非法相对路径') }
        Assert-True $threw '危险路径未被拒绝。'
    }

    Invoke-TestCase '缺少升级契约时拒绝' {
        $package = New-TestPackage (Join-Path $testRoot 'missing-upgrade')
        $package.manifest.Remove('upgrade')
        $utf8 = New-Object Text.UTF8Encoding($false)
        $manifestPath = Join-Path $package.root 'release-manifest.json'
        [IO.File]::WriteAllText($manifestPath, ($package.manifest | ConvertTo-Json -Depth 12), $utf8)
        [IO.File]::WriteAllBytes((Join-Path $package.root 'release-manifest.sig'), [KanyikanTestSigning]::Sign($package.privateKey, [IO.File]::ReadAllBytes($manifestPath)))
        $threw = $false
        try { Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 $package.trustedHash | Out-Null } catch { $threw = $_.Exception.Message.Contains('缺少字段 upgrade') }
        Assert-True $threw '缺少升级契约的发行包未被拒绝。'
    }

    Invoke-TestCase '升级冒烟检查顺序被修改时拒绝' {
        $package = New-TestPackage (Join-Path $testRoot 'wrong-smoke-order')
        $package.manifest.upgrade.smokeTests = @('https_ready', 'https_health', 'admin_login', 'core_api')
        $utf8 = New-Object Text.UTF8Encoding($false)
        $manifestPath = Join-Path $package.root 'release-manifest.json'
        [IO.File]::WriteAllText($manifestPath, ($package.manifest | ConvertTo-Json -Depth 12), $utf8)
        [IO.File]::WriteAllBytes((Join-Path $package.root 'release-manifest.sig'), [KanyikanTestSigning]::Sign($package.privateKey, [IO.File]::ReadAllBytes($manifestPath)))
        $threw = $false
        try { Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 $package.trustedHash | Out-Null } catch { $threw = $_.Exception.Message.Contains('冒烟检查') }
        Assert-True $threw '冒烟检查顺序被修改的发行包未被拒绝。'
    }

    Invoke-TestCase '发行清单试图覆盖本机生成配置时拒绝' {
        $package = New-TestPackage (Join-Path $testRoot 'reserved-generated-path')
        $package.manifest.files[6].path = 'config/system.env'
        $utf8 = New-Object Text.UTF8Encoding($false)
        $manifestPath = Join-Path $package.root 'release-manifest.json'
        [IO.File]::WriteAllText($manifestPath, ($package.manifest | ConvertTo-Json -Depth 12), $utf8)
        [IO.File]::WriteAllBytes((Join-Path $package.root 'release-manifest.sig'), [KanyikanTestSigning]::Sign($package.privateKey, [IO.File]::ReadAllBytes($manifestPath)))
        $threw = $false
        try { Test-KanyikanReleasePackage -PackageRoot $package.root -TrustedPublicKeySha256 $package.trustedHash | Out-Null } catch { $threw = $_.Exception.Message.Contains('安装器生成路径') }
        Assert-True $threw '覆盖本机生成配置的发行清单未被拒绝。'
    }
}
finally {
    if ([IO.Directory]::Exists($testRoot)) { [IO.Directory]::Delete($testRoot, $true) }
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
