Set-StrictMode -Version 2.0

if (-not ('KanyikanNativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class KanyikanNativeMethods
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileEx(
        string existingFileName,
        string newFileName,
        int flags
    );
}
'@
}

if (-not ('KanyikanReleaseSignature' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Security.Cryptography;

public static class KanyikanReleaseSignature
{
    private static int ReadLength(byte[] data, ref int offset)
    {
        int first = data[offset++];
        if ((first & 0x80) == 0) return first;
        int count = first & 0x7f;
        if (count < 1 || count > 4 || offset + count > data.Length) throw new InvalidDataException("Invalid DER length.");
        int length = 0;
        for (int i = 0; i < count; i++) length = (length << 8) | data[offset++];
        return length;
    }

    private static byte[] ReadInteger(byte[] data, ref int offset)
    {
        if (offset >= data.Length || data[offset++] != 0x02) throw new InvalidDataException("Expected DER integer.");
        int length = ReadLength(data, ref offset);
        if (length < 1 || offset + length > data.Length) throw new InvalidDataException("Invalid DER integer.");
        int start = offset;
        if (length > 1 && data[start] == 0) { start++; length--; }
        byte[] value = new byte[length];
        Buffer.BlockCopy(data, start, value, 0, length);
        offset += (start - offset) + length;
        return value;
    }

    public static bool Verify(byte[] publicKeyPem, byte[] content, byte[] signature)
    {
        string pem = System.Text.Encoding.ASCII.GetString(publicKeyPem).Replace("\r", "").Trim();
        const string begin = "-----BEGIN RSA PUBLIC KEY-----\n";
        const string end = "\n-----END RSA PUBLIC KEY-----";
        if (!pem.StartsWith(begin, StringComparison.Ordinal) || !pem.EndsWith(end, StringComparison.Ordinal))
            throw new InvalidDataException("public-key.pem must contain one PKCS#1 RSA public key.");
        string base64 = pem.Substring(begin.Length, pem.Length - begin.Length - end.Length).Replace("\n", "");
        byte[] der = Convert.FromBase64String(base64);
        int offset = 0;
        if (der[offset++] != 0x30) throw new InvalidDataException("Expected DER sequence.");
        int sequenceLength = ReadLength(der, ref offset);
        if (sequenceLength != der.Length - offset) throw new InvalidDataException("Invalid DER sequence length.");
        RSAParameters parameters = new RSAParameters { Modulus = ReadInteger(der, ref offset), Exponent = ReadInteger(der, ref offset) };
        if (offset != der.Length) throw new InvalidDataException("Unexpected RSA public key data.");
        using (RSACryptoServiceProvider rsa = new RSACryptoServiceProvider())
        {
            rsa.PersistKeyInCsp = false;
            rsa.ImportParameters(parameters);
            return rsa.VerifyData(content, CryptoConfig.MapNameToOID("SHA256"), signature);
        }
    }
}
'@
}

if (-not ('KanyikanPinnedHttps' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

public static class KanyikanPinnedHttps
{
    public static int GetStatusCode(string url, byte[] expectedSha256, int timeoutMilliseconds)
    {
        HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
        request.Method = "GET";
        request.Timeout = timeoutMilliseconds;
        request.ReadWriteTimeout = timeoutMilliseconds;
        request.Proxy = null;
        request.AllowAutoRedirect = false;
        request.ServerCertificateValidationCallback = (sender, certificate, chain, errors) =>
        {
            if (certificate == null) return false;
            using (SHA256 sha256 = SHA256.Create())
            {
                byte[] actual = sha256.ComputeHash(certificate.GetRawCertData());
                if (actual.Length != expectedSha256.Length) return false;
                int difference = 0;
                for (int i = 0; i < actual.Length; i++) difference |= actual[i] ^ expectedSha256[i];
                return difference == 0;
            }
        };
        using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
        {
            return (int)response.StatusCode;
        }
    }
}
'@
}

$script:ContractVersion = 1
$script:ComposeProjectName = 'kanyikan'
$script:InstallStates = @(
    'NEW',
    'PREFLIGHT_OK',
    'VERIFIED',
    'IMAGES_LOADED',
    'CONFIG_CREATED',
    'CERT_READY',
    'SERVICES_STARTING',
    'HEALTHY',
    'INSTALLED'
)
$script:OwnedResources = [ordered]@{
    network = 'kanyikan_internal'
    volumes = [ordered]@{
        postgres = 'kanyikan_postgres_data'
        redis = 'kanyikan_redis_data'
        snapshots = 'kanyikan_snapshots_data'
        skills = 'kanyikan_skills_data'
    }
}

function Get-KanyikanNormalizedPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Get-KanyikanStatePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    return [System.IO.Path]::Combine(
        (Get-KanyikanNormalizedPath -Path $InstallRoot),
        'state',
        'install-state.json'
    )
}

function New-KanyikanInstallState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    return [pscustomobject][ordered]@{
        contractVersion = $script:ContractVersion
        productVersion = $null
        currentState = 'NEW'
        updatedAt = [DateTime]::UtcNow.ToString('o')
        installRoot = Get-KanyikanNormalizedPath -Path $InstallRoot
        manifestSha256 = $null
        releasePublicKeySha256 = $null
        composeProjectName = $script:ComposeProjectName
        resources = [pscustomobject][ordered]@{
            network = $script:OwnedResources.network
            volumes = [pscustomobject][ordered]@{
                postgres = $script:OwnedResources.volumes.postgres
                redis = $script:OwnedResources.volumes.redis
                snapshots = $script:OwnedResources.volumes.snapshots
                skills = $script:OwnedResources.volumes.skills
            }
        }
        images = @()
        caThumbprint = $null
        lastFailure = $null
    }
}

function Protect-KanyikanText {
    param(
        [AllowNull()]
        [string]$Text
    )

    if ([string]::IsNullOrEmpty($Text)) {
        return $Text
    }

    $protected = $Text
    $patterns = @(
        '(?is)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----',
        '(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+',
        '(?i)\b(SECRET_KEY|CONFIG_ENCRYPTION_KEY|POSTGRES_PASSWORD|REDIS_PASSWORD|BROWSERLESS_TOKEN|PASSWORD|API_KEY|COOKIE|JWT)\s*[:=]\s*([^\s;,]+)',
        '(?i)(https?://[^\s:/@]+:)[^\s/@]+(@)'
    )
    $replacements = @(
        '[REDACTED]',
        '${1}[REDACTED]',
        '${1}=[REDACTED]',
        '${1}[REDACTED]${2}'
    )

    for ($index = 0; $index -lt $patterns.Count; $index++) {
        $protected = [regex]::Replace($protected, $patterns[$index], $replacements[$index])
    }
    return $protected
}

function Assert-KanyikanInstallState {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$State,

        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    $requiredProperties = @(
        'contractVersion',
        'productVersion',
        'currentState',
        'updatedAt',
        'installRoot',
        'manifestSha256',
        'releasePublicKeySha256',
        'composeProjectName',
        'resources',
        'images',
        'caThumbprint',
        'lastFailure'
    )
    foreach ($propertyName in $requiredProperties) {
        if ($null -eq $State.PSObject.Properties[$propertyName]) {
            throw "安装状态损坏：缺少字段 $propertyName。"
        }
    }

    if ([int]$State.contractVersion -ne $script:ContractVersion) {
        throw '安装状态损坏：契约版本不受支持。'
    }
    if ($script:InstallStates -notcontains [string]$State.currentState) {
        throw '安装状态损坏：状态值不合法。'
    }
    if ([string]$State.composeProjectName -cne $script:ComposeProjectName) {
        throw '安装状态损坏：Compose 项目归属不匹配。'
    }

    $expectedRoot = Get-KanyikanNormalizedPath -Path $InstallRoot
    $recordedRoot = Get-KanyikanNormalizedPath -Path ([string]$State.installRoot)
    if (-not [string]::Equals($expectedRoot, $recordedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw '安装状态损坏：安装根目录与状态记录不一致。'
    }

    $expectedVolumes = $script:OwnedResources.volumes
    if ($null -eq $State.resources -or $null -eq $State.resources.volumes) {
        throw '安装状态损坏：缺少资源归属。'
    }
    if ([string]$State.resources.network -cne $script:OwnedResources.network) {
        throw '安装状态损坏：网络资源归属不匹配。'
    }
    foreach ($volumeName in $expectedVolumes.Keys) {
        $actualProperty = $State.resources.volumes.PSObject.Properties[$volumeName]
        if ($null -eq $actualProperty -or [string]$actualProperty.Value -cne [string]$expectedVolumes[$volumeName]) {
            throw "安装状态损坏：数据卷资源归属不匹配（$volumeName）。"
        }
    }

    $parsedTimestamp = [DateTime]::MinValue
    if (-not [DateTime]::TryParse(
        [string]$State.updatedAt,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsedTimestamp
    )) {
        throw '安装状态损坏：更新时间格式不合法。'
    }
}

function Read-KanyikanInstallState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    $statePath = Get-KanyikanStatePath -InstallRoot $InstallRoot
    if (-not [System.IO.File]::Exists($statePath)) {
        return New-KanyikanInstallState -InstallRoot $InstallRoot
    }

    try {
        $rawState = [System.IO.File]::ReadAllText($statePath, [Text.Encoding]::UTF8)
        $state = $rawState | ConvertFrom-Json
        Assert-KanyikanInstallState -State $state -InstallRoot $InstallRoot
        return $state
    }
    catch {
        $reason = Protect-KanyikanText -Text $_.Exception.Message
        throw "安装状态损坏，已停止写操作。请从有效备份恢复。原因：$reason"
    }
}

function Write-KanyikanInstallState {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$State,

        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    Assert-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    $statePath = Get-KanyikanStatePath -InstallRoot $InstallRoot
    $stateDirectory = [System.IO.Path]::GetDirectoryName($statePath)
    [System.IO.Directory]::CreateDirectory($stateDirectory) | Out-Null
    $temporaryPath = [System.IO.Path]::Combine(
        $stateDirectory,
        ".install-state.$([Guid]::NewGuid().ToString('N')).tmp"
    )
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)

    try {
        $json = $State | ConvertTo-Json -Depth 12
        [System.IO.File]::WriteAllText($temporaryPath, $json, $utf8WithoutBom)
        $replaceExistingAndWriteThrough = 0x1 -bor 0x8
        if (-not [KanyikanNativeMethods]::MoveFileEx(
            $temporaryPath,
            $statePath,
            $replaceExistingAndWriteThrough
        )) {
            $win32Error = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw (New-Object ComponentModel.Win32Exception($win32Error))
        }
    }
    finally {
        if ([System.IO.File]::Exists($temporaryPath)) {
            [System.IO.File]::Delete($temporaryPath)
        }
    }
}

function Set-KanyikanInstallState {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$State,

        [Parameter(Mandatory = $true)]
        [ValidateSet(
            'PREFLIGHT_OK',
            'VERIFIED',
            'IMAGES_LOADED',
            'CONFIG_CREATED',
            'CERT_READY',
            'SERVICES_STARTING',
            'HEALTHY',
            'INSTALLED'
        )]
        [string]$NextState,

        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    Assert-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    $currentIndex = [Array]::IndexOf($script:InstallStates, [string]$State.currentState)
    $nextIndex = [Array]::IndexOf($script:InstallStates, $NextState)
    if ($nextIndex -ne ($currentIndex + 1)) {
        throw "非法安装状态迁移：$($State.currentState) -> $NextState。"
    }

    $State.currentState = $NextState
    $State.updatedAt = [DateTime]::UtcNow.ToString('o')
    $State.lastFailure = $null
    Write-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    return $State
}

function Set-KanyikanInstallFailure {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$State,

        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string]$Stage,

        [Parameter(Mandatory = $true)]
        [int]$ExitCode,

        [Parameter(Mandatory = $true)]
        [string]$Reason,

        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    Assert-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    $State.updatedAt = [DateTime]::UtcNow.ToString('o')
    $State.lastFailure = [pscustomobject][ordered]@{
        occurredAt = [DateTime]::UtcNow.ToString('o')
        command = Protect-KanyikanText -Text $Command
        stage = Protect-KanyikanText -Text $Stage
        exitCode = $ExitCode
        reason = Protect-KanyikanText -Text $Reason
    }
    Write-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    return $State
}

function Get-KanyikanInstallStates {
    return @($script:InstallStates)
}

function Invoke-KanyikanDockerCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & docker.exe @Arguments 2>&1
    return [pscustomobject]@{
        exitCode = $LASTEXITCODE
        output = (@($output) -join [Environment]::NewLine).Trim()
    }
}

function Get-KanyikanHostFacts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    $normalizedRoot = Get-KanyikanNormalizedPath -Path $InstallRoot
    $architecture = if ([Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432') -eq 'AMD64') {
        'AMD64'
    }
    else {
        [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
    }
    $isWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $windowsMajorVersion = [Environment]::OSVersion.Version.Major
    $powerShellEdition = if (Test-Path variable:PSEdition) { $PSEdition } else { 'Desktop' }

    $memoryBytes = 0L
    if ($isWindows) {
        try {
            $computerSystem = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
            $memoryBytes = [int64]$computerSystem.TotalPhysicalMemory
        }
        catch {
            $memoryBytes = 0L
        }
    }

    $freeDiskBytes = 0L
    try {
        $pathRoot = [System.IO.Path]::GetPathRoot($normalizedRoot)
        $freeDiskBytes = [int64](New-Object System.IO.DriveInfo($pathRoot)).AvailableFreeSpace
    }
    catch {
        $freeDiskBytes = 0L
    }

    $installRootWritable = $false
    try {
        if ([System.IO.Directory]::Exists($normalizedRoot)) {
            $probePath = [System.IO.Path]::Combine(
                $normalizedRoot,
                ".kanyikan-write-probe-$([Guid]::NewGuid().ToString('N')).tmp"
            )
            try {
                [System.IO.File]::WriteAllBytes($probePath, [byte[]]@(75, 89, 75))
                $installRootWritable = $true
            }
            finally {
                if ([System.IO.File]::Exists($probePath)) {
                    [System.IO.File]::Delete($probePath)
                }
            }
        }
    }
    catch {
        $installRootWritable = $false
    }

    $portAvailable = $true
    try {
        $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        $portAvailable = @($listeners | Where-Object { $_.Port -eq 10443 }).Count -eq 0
    }
    catch {
        $portAvailable = $false
    }

    $dockerDesktopPath = [System.IO.Path]::Combine(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles),
        'Docker',
        'Docker',
        'Docker Desktop.exe'
    )
    $dockerDesktopInstalled = [System.IO.File]::Exists($dockerDesktopPath)
    $dockerCliAvailable = $null -ne (Get-Command docker.exe -ErrorAction SilentlyContinue)
    $dockerEngineAvailable = $false
    $dockerOsType = $null
    $composeMajorVersion = 0
    $dockerProxyEnabled = $false

    if ($dockerCliAvailable) {
        $engine = Invoke-KanyikanDockerCommand -Arguments @('info', '--format', '{{.OSType}}')
        if ($engine.exitCode -eq 0) {
            $dockerEngineAvailable = $true
            $dockerOsType = $engine.output.Trim().ToLowerInvariant()

            $proxy = Invoke-KanyikanDockerCommand -Arguments @(
                'info',
                '--format',
                '{{if or .HTTPProxy .HTTPSProxy}}true{{else}}false{{end}}'
            )
            $dockerProxyEnabled = $proxy.exitCode -eq 0 -and $proxy.output.Trim() -ceq 'true'
        }

        $compose = Invoke-KanyikanDockerCommand -Arguments @('compose', 'version', '--short')
        if ($compose.exitCode -eq 0 -and $compose.output -match '^v?([0-9]+)\.') {
            $composeMajorVersion = [int]$Matches[1]
        }
    }

    return [pscustomobject][ordered]@{
        isWindows = $isWindows
        windowsMajorVersion = $windowsMajorVersion
        architecture = $architecture
        powerShellEdition = $powerShellEdition
        powerShellVersion = $PSVersionTable.PSVersion
        dockerDesktopInstalled = $dockerDesktopInstalled
        dockerCliAvailable = $dockerCliAvailable
        dockerEngineAvailable = $dockerEngineAvailable
        dockerOsType = $dockerOsType
        composeMajorVersion = $composeMajorVersion
        cpuCores = [Environment]::ProcessorCount
        memoryBytes = $memoryBytes
        freeDiskBytes = $freeDiskBytes
        portAvailable = $portAvailable
        installRootWritable = $installRootWritable
        dockerProxyEnabled = $dockerProxyEnabled
    }
}

function Test-KanyikanPreflightFacts {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Facts,

        [switch]$AllowOwnedEntrypoint
    )

    $checks = @(
        [pscustomobject]@{ name = 'Windows 10/11 x64'; passed = ($Facts.isWindows -and $Facts.windowsMajorVersion -eq 10 -and $Facts.architecture -ceq 'AMD64'); exitCode = 20; remediation = '请使用 Windows 10/11 x64。' },
        [pscustomobject]@{ name = 'Windows PowerShell 5.1+'; passed = ($Facts.powerShellEdition -ceq 'Desktop' -and $Facts.powerShellVersion -ge [Version]'5.1'); exitCode = 20; remediation = '请使用 Windows PowerShell 5.1 或更高版本。' },
        [pscustomobject]@{ name = 'Docker Desktop'; passed = [bool]$Facts.dockerDesktopInstalled; exitCode = 21; remediation = '请安装 Docker Desktop 后重试。' },
        [pscustomobject]@{ name = 'Docker CLI'; passed = [bool]$Facts.dockerCliAvailable; exitCode = 21; remediation = '请修复 Docker Desktop 安装，使 docker.exe 可用。' },
        [pscustomobject]@{ name = 'Docker Engine'; passed = [bool]$Facts.dockerEngineAvailable; exitCode = 21; remediation = '请启动 Docker Desktop 并等待 Engine 就绪。' },
        [pscustomobject]@{ name = 'Linux Containers'; passed = ([string]$Facts.dockerOsType -ceq 'linux'); exitCode = 21; remediation = '请将 Docker Desktop 切换为 Linux Containers。' },
        [pscustomobject]@{ name = 'Docker Compose v2'; passed = ([int]$Facts.composeMajorVersion -eq 2); exitCode = 21; remediation = '请启用 Docker Compose v2。' },
        [pscustomobject]@{ name = 'CPU >= 4'; passed = ([int]$Facts.cpuCores -ge 4); exitCode = 22; remediation = '至少需要 4 个逻辑处理器。' },
        [pscustomobject]@{ name = 'Memory >= 8 GiB'; passed = ([int64]$Facts.memoryBytes -ge 8589934592L); exitCode = 22; remediation = '至少需要 8 GiB 物理内存。' },
        [pscustomobject]@{ name = 'Disk >= 20 GiB'; passed = ([int64]$Facts.freeDiskBytes -ge 21474836480L); exitCode = 22; remediation = '安装卷至少需要 20 GiB 可用空间。' },
        [pscustomobject]@{ name = '127.0.0.1:10443 available'; passed = ([bool]$Facts.portAvailable -or $AllowOwnedEntrypoint); exitCode = 22; remediation = '请释放本机 TCP 端口 10443；安装器不会结束占用进程。' },
        [pscustomobject]@{ name = 'Install root writable'; passed = [bool]$Facts.installRootWritable; exitCode = 22; remediation = '请确认当前用户可写安装目录。' }
    )

    $failedCheck = $checks | Where-Object { -not $_.passed } | Select-Object -First 1
    return [pscustomobject][ordered]@{
        passed = $null -eq $failedCheck
        exitCode = if ($null -eq $failedCheck) { 0 } else { $failedCheck.exitCode }
        failedCheck = if ($null -eq $failedCheck) { $null } else { $failedCheck.name }
        remediation = if ($null -eq $failedCheck) { $null } else { $failedCheck.remediation }
        proxyEnabled = [bool]$Facts.dockerProxyEnabled
        checks = $checks
    }
}

function Invoke-KanyikanPreflight {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot,

        [switch]$AllowOwnedEntrypoint
    )

    $facts = Get-KanyikanHostFacts -InstallRoot $InstallRoot
    return Test-KanyikanPreflightFacts -Facts $facts -AllowOwnedEntrypoint:$AllowOwnedEntrypoint
}

function Get-KanyikanFileSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Assert-KanyikanPackageRelativePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (
        [string]::IsNullOrWhiteSpace($Path) -or
        $Path.Length -gt 240 -or
        $Path.Contains('\') -or
        $Path.Contains('//') -or
        $Path.EndsWith('/') -or
        [System.IO.Path]::IsPathRooted($Path) -or
        $Path -match '(^|/)\.\.?(/|$)' -or
        $Path -match '[:<>"|?*\x00-\x1f]'
    ) {
        throw "发行清单包含非法相对路径：$Path"
    }
}

function Assert-KanyikanExactProperties {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Value,

        [Parameter(Mandatory = $true)]
        [string[]]$Names,

        [Parameter(Mandatory = $true)]
        [string]$Context
    )

    $actualNames = @($Value.PSObject.Properties.Name)
    foreach ($name in $Names) {
        if ($actualNames -notcontains $name) { throw "$Context 缺少字段 $name。" }
    }
    foreach ($name in $actualNames) {
        if ($Names -notcontains $name) { throw "$Context 包含未允许字段 $name。" }
    }
}

function Assert-KanyikanReleaseManifest {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Manifest
    )

    Assert-KanyikanExactProperties -Value $Manifest -Names @(
        'schemaVersion', 'product', 'release', 'target', 'requirements', 'entrypoint',
        'tls', 'compose', 'resources', 'files', 'images', 'signing'
    ) -Context 'manifest'
    if ($Manifest.schemaVersion -ne 1 -or $Manifest.product -cne 'Kanyikan') { throw 'manifest 产品或契约版本不合法。' }

    Assert-KanyikanExactProperties -Value $Manifest.release -Names @('version', 'publishedAt', 'sourceCommit', 'packageType') -Context 'release'
    if ([string]$Manifest.release.version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$') { throw 'release.version 不是合法语义化版本。' }
    if ([string]$Manifest.release.sourceCommit -notmatch '^[0-9a-f]{40}$' -or $Manifest.release.packageType -cne 'offline') { throw 'release 元数据不合法。' }
    $publishedAt = [DateTime]::MinValue
    if (-not [DateTime]::TryParse([string]$Manifest.release.publishedAt, [ref]$publishedAt)) { throw 'release.publishedAt 不合法。' }

    Assert-KanyikanExactProperties -Value $Manifest.target -Names @('os', 'architecture', 'dockerPlatform', 'deploymentProfile') -Context 'target'
    if ($Manifest.target.os -cne 'windows' -or $Manifest.target.architecture -cne 'amd64' -or $Manifest.target.dockerPlatform -cne 'linux/amd64' -or $Manifest.target.deploymentProfile -cne 'local_appliance') { throw '发行目标必须是 Windows amd64 本地设备。' }

    Assert-KanyikanExactProperties -Value $Manifest.requirements -Names @('windowsEditions', 'powershellMinimumVersion', 'dockerDesktopRequired', 'composeMajorVersion', 'minimumCpuCores', 'minimumMemoryBytes', 'minimumFreeDiskBytes') -Context 'requirements'
    if (@($Manifest.requirements.windowsEditions).Count -ne 2 -or $Manifest.requirements.windowsEditions[0] -cne '10' -or $Manifest.requirements.windowsEditions[1] -cne '11' -or $Manifest.requirements.powershellMinimumVersion -cne '5.1' -or -not $Manifest.requirements.dockerDesktopRequired -or $Manifest.requirements.composeMajorVersion -ne 2 -or $Manifest.requirements.minimumCpuCores -ne 4 -or $Manifest.requirements.minimumMemoryBytes -ne 8589934592L -or $Manifest.requirements.minimumFreeDiskBytes -ne 21474836480L) { throw 'requirements 与 V1 契约不一致。' }

    Assert-KanyikanExactProperties -Value $Manifest.entrypoint -Names @('scheme', 'host', 'port', 'path') -Context 'entrypoint'
    if ($Manifest.entrypoint.scheme -cne 'https' -or $Manifest.entrypoint.host -cne '127.0.0.1' -or $Manifest.entrypoint.port -ne 10443 -or $Manifest.entrypoint.path -cne '/') { throw '唯一入口与 V1 契约不一致。' }

    Assert-KanyikanExactProperties -Value $Manifest.tls -Names @('leafValidityDays', 'caValidityDays') -Context 'tls'
    if ($Manifest.tls.leafValidityDays -lt 1 -or $Manifest.tls.leafValidityDays -gt 825 -or $Manifest.tls.caValidityDays -lt 825 -or $Manifest.tls.caValidityDays -gt 3650 -or $Manifest.tls.caValidityDays -le $Manifest.tls.leafValidityDays) { throw 'TLS 有效期不合法。' }

    Assert-KanyikanExactProperties -Value $Manifest.compose -Names @('path', 'projectName', 'pullPolicy', 'services') -Context 'compose'
    $expectedServices = @('postgres', 'redis', 'backend', 'worker', 'crawler', 'beat', 'outbox-relay', 'frontend', 'nginx', 'browserless')
    if ($Manifest.compose.path -cne 'compose.release.yml' -or $Manifest.compose.projectName -cne 'kanyikan' -or $Manifest.compose.pullPolicy -cne 'never' -or @($Manifest.compose.services).Count -ne 10) { throw 'Compose 契约不合法。' }
    foreach ($service in $expectedServices) { if (@($Manifest.compose.services) -notcontains $service) { throw "Compose 缺少服务 $service。" } }

    Assert-KanyikanExactProperties -Value $Manifest.resources -Names @('namedVolumes') -Context 'resources'
    Assert-KanyikanExactProperties -Value $Manifest.resources.namedVolumes -Names @('postgres', 'redis', 'snapshots', 'skills') -Context 'namedVolumes'
    $expectedVolumes = $script:OwnedResources.volumes
    foreach ($volume in $expectedVolumes.Keys) { if ([string]$Manifest.resources.namedVolumes.$volume -cne [string]$expectedVolumes[$volume]) { throw "数据卷 $volume 名称不合法。" } }

    $requiredFiles = [ordered]@{
        'install.cmd' = 'entrypoint'; 'kanyikan.ps1' = 'controller'; 'lib/Kanyikan.Installer.psm1' = 'module';
        'compose.release.yml' = 'compose'; 'images/kanyikan-images-windows-amd64.tar' = 'image_archive';
        'config/system.env.template' = 'config_template'; 'docs/快速安装说明.md' = 'documentation';
        'docs/故障排查.md' = 'documentation'; 'docs/第三方许可证.html' = 'documentation';
        'public-key.pem' = 'public_key'; 'VERSION' = 'version'; 'LICENSE' = 'license'
    }
    if (@($Manifest.files).Count -lt 12) { throw '发行文件清单少于 12 项。' }
    $seenFiles = @{}
    foreach ($file in @($Manifest.files)) {
        Assert-KanyikanExactProperties -Value $file -Names @('path', 'role', 'sizeBytes', 'sha256') -Context 'files[]'
        Assert-KanyikanPackageRelativePath -Path ([string]$file.path)
        if ($seenFiles.ContainsKey([string]$file.path)) { throw "发行文件路径重复：$($file.path)" }
        $seenFiles[[string]$file.path] = $true
        if ($file.sizeBytes -lt 1 -or [string]$file.sha256 -notmatch '^[0-9a-f]{64}$') { throw "发行文件摘要元数据不合法：$($file.path)" }
    }
    foreach ($requiredPath in $requiredFiles.Keys) {
        $entry = @($Manifest.files | Where-Object { $_.path -ceq $requiredPath -and $_.role -ceq $requiredFiles[$requiredPath] })
        if ($entry.Count -ne 1) { throw "发行文件清单缺少或错误声明：$requiredPath" }
    }

    Assert-KanyikanExactProperties -Value $Manifest.images -Names @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless') -Context 'images'
    $imageServices = [ordered]@{
        backend = @('backend', 'worker', 'crawler', 'beat', 'outbox-relay'); frontend = @('frontend');
        postgres = @('postgres'); redis = @('redis'); nginx = @('nginx'); browserless = @('browserless')
    }
    foreach ($imageName in $imageServices.Keys) {
        $image = $Manifest.images.$imageName
        Assert-KanyikanExactProperties -Value $image -Names @('reference', 'digest', 'imageId', 'platform', 'archivePath', 'composeServices') -Context "images.$imageName"
        if ([string]$image.digest -notmatch '^sha256:[0-9a-f]{64}$' -or [string]$image.imageId -notmatch '^sha256:[0-9a-f]{64}$' -or -not ([string]$image.reference).EndsWith("@$($image.digest)") -or $image.platform -cne 'linux/amd64' -or $image.archivePath -cne 'images/kanyikan-images-windows-amd64.tar') { throw "镜像 $imageName 元数据不合法。" }
        $actualServices = @($image.composeServices)
        if ($actualServices.Count -ne $imageServices[$imageName].Count) { throw "镜像 $imageName 服务映射不合法。" }
        foreach ($service in $imageServices[$imageName]) { if ($actualServices -notcontains $service) { throw "镜像 $imageName 缺少服务映射 $service。" } }
    }

    Assert-KanyikanExactProperties -Value $Manifest.signing -Names @('algorithm', 'keyId', 'publicKeySha256', 'publicKeyPath', 'signaturePath') -Context 'signing'
    if ($Manifest.signing.algorithm -cne 'RSASSA-PKCS1-v1_5-SHA256' -or [string]$Manifest.signing.keyId -notmatch '^[A-Za-z0-9._-]{1,128}$' -or [string]$Manifest.signing.publicKeySha256 -notmatch '^[0-9a-f]{64}$' -or $Manifest.signing.publicKeyPath -cne 'public-key.pem' -or $Manifest.signing.signaturePath -cne 'release-manifest.sig') { throw '签名元数据不合法。' }
}

function Test-KanyikanReleasePackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageRoot,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{64}$')]
        [string]$TrustedPublicKeySha256
    )

    $root = Get-KanyikanNormalizedPath -Path $PackageRoot
    $controlPaths = @('release-manifest.json', 'release-manifest.sig', 'manifest.sha256', 'public-key.pem', 'VERSION')
    foreach ($relativePath in $controlPaths) {
        if (-not [System.IO.File]::Exists([System.IO.Path]::Combine($root, $relativePath))) { throw "发行包缺少文件：$relativePath" }
    }

    $manifestPath = [System.IO.Path]::Combine($root, 'release-manifest.json')
    $signaturePath = [System.IO.Path]::Combine($root, 'release-manifest.sig')
    $publicKeyPath = [System.IO.Path]::Combine($root, 'public-key.pem')
    $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
    try { $manifest = ([Text.Encoding]::UTF8.GetString($manifestBytes)) | ConvertFrom-Json }
    catch { throw "release-manifest.json 不是合法 JSON：$($_.Exception.Message)" }
    Assert-KanyikanReleaseManifest -Manifest $manifest

    $publicKeySha256 = Get-KanyikanFileSha256 -Path $publicKeyPath
    if ($publicKeySha256 -cne $TrustedPublicKeySha256 -or $publicKeySha256 -cne [string]$manifest.signing.publicKeySha256) { throw '发行公钥指纹与固定信任锚不匹配。' }
    $signature = [System.IO.File]::ReadAllBytes($signaturePath)
    if (-not [KanyikanReleaseSignature]::Verify([System.IO.File]::ReadAllBytes($publicKeyPath), $manifestBytes, $signature)) { throw 'release-manifest.json 的 RSA-SHA256 签名无效。' }

    $checksumPath = [System.IO.Path]::Combine($root, 'manifest.sha256')
    $checksumLines = [System.IO.File]::ReadAllLines($checksumPath, [Text.Encoding]::UTF8)
    $declaredChecksums = @{}
    foreach ($line in $checksumLines) {
        if ($line -notmatch '^([0-9a-f]{64})  ([^\r\n]+)$') { throw 'manifest.sha256 格式不合法。' }
        Assert-KanyikanPackageRelativePath -Path $Matches[2]
        if ($declaredChecksums.ContainsKey($Matches[2])) { throw "manifest.sha256 路径重复：$($Matches[2])" }
        $declaredChecksums[$Matches[2]] = $Matches[1]
    }
    if ($declaredChecksums.Count -ne @($manifest.files).Count) { throw 'manifest.sha256 与发行文件清单数量不一致。' }

    $rootPrefix = $root + [System.IO.Path]::DirectorySeparatorChar
    foreach ($file in @($manifest.files)) {
        $relativePath = [string]$file.path
        if (-not $declaredChecksums.ContainsKey($relativePath) -or $declaredChecksums[$relativePath] -cne [string]$file.sha256) { throw "manifest.sha256 与 manifest 不一致：$relativePath" }
        $fullPath = Get-KanyikanNormalizedPath -Path ([System.IO.Path]::Combine($root, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)))
        if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or -not [System.IO.File]::Exists($fullPath)) { throw "发行文件缺失或越界：$relativePath" }
        $fileInfo = New-Object System.IO.FileInfo($fullPath)
        if ($fileInfo.Length -ne [int64]$file.sizeBytes -or (Get-KanyikanFileSha256 -Path $fullPath) -cne [string]$file.sha256) { throw "发行文件大小或 SHA256 不匹配：$relativePath" }
    }

    $version = [System.IO.File]::ReadAllText([System.IO.Path]::Combine($root, 'VERSION'), [Text.Encoding]::UTF8).Trim()
    if ($version -cne [string]$manifest.release.version) { throw 'VERSION 与 release.version 不一致。' }
    return [pscustomobject][ordered]@{
        passed = $true
        version = $version
        manifestSha256 = Get-KanyikanFileSha256 -Path $manifestPath
        releasePublicKeySha256 = $publicKeySha256
        manifest = $manifest
    }
}

function Test-KanyikanLoadedImageFacts {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Manifest,

        [Parameter(Mandatory = $true)]
        [string[]]$LoadedReferences,

        [Parameter(Mandatory = $true)]
        [psobject[]]$InspectedImages
    )

    $imageNames = @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless')
    $expectedReferences = @($imageNames | ForEach-Object { [string]$Manifest.images.$_.reference })
    $uniqueLoadedReferences = @($LoadedReferences | Sort-Object -Unique)
    if ($uniqueLoadedReferences.Count -ne 6) {
        throw "离线镜像包必须恰好加载 6 个镜像，实际为 $($uniqueLoadedReferences.Count) 个。"
    }
    foreach ($reference in $uniqueLoadedReferences) {
        if ($expectedReferences -cnotcontains $reference) { throw "离线镜像包包含未声明镜像：$reference" }
    }
    foreach ($reference in $expectedReferences) {
        if ($uniqueLoadedReferences -cnotcontains $reference) { throw "离线镜像包缺少声明镜像：$reference" }
    }

    $verifiedImages = @()
    foreach ($imageName in $imageNames) {
        $expected = $Manifest.images.$imageName
        $matches = @($InspectedImages | Where-Object { $_.reference -ceq [string]$expected.reference })
        if ($matches.Count -ne 1) { throw "无法唯一核对镜像：$imageName" }
        $actual = $matches[0]
        if ([string]$actual.imageId -cne [string]$expected.imageId) { throw "镜像 ID 不匹配：$imageName" }
        if ([string]$actual.os -cne 'linux' -or [string]$actual.architecture -cne 'amd64') { throw "镜像平台不是 linux/amd64：$imageName" }
        if (@($actual.repoDigests) -cnotcontains [string]$expected.reference) { throw "镜像 RepoDigest 不匹配：$imageName" }
        $verifiedImages += [pscustomobject][ordered]@{
            name = $imageName
            reference = [string]$expected.reference
            imageId = [string]$actual.imageId
            platform = 'linux/amd64'
        }
    }
    return $verifiedImages
}

function Import-KanyikanReleaseImages {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Manifest,

        [Parameter(Mandatory = $true)]
        [string]$PackageRoot
    )

    $root = Get-KanyikanNormalizedPath -Path $PackageRoot
    $archivePath = [System.IO.Path]::Combine(
        $root,
        ([string]$Manifest.images.backend.archivePath).Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    )
    $loadResult = Invoke-KanyikanDockerCommand -Arguments @('load', '--input', $archivePath)
    if ($loadResult.exitCode -ne 0) { throw "docker load 失败：$(Protect-KanyikanText -Text $loadResult.output)" }

    $loadedReferences = @()
    foreach ($line in ($loadResult.output -split '\r?\n')) {
        if ($line -match '^Loaded image:\s+(.+@sha256:[0-9a-f]{64})$') {
            $loadedReferences += $Matches[1].Trim()
        }
        elseif ($line -match '^Loaded image ID:') {
            throw '离线镜像包包含无法映射到声明 RepoDigest 的镜像。'
        }
    }

    $inspectedImages = Get-KanyikanInspectedImages -Manifest $Manifest
    return Test-KanyikanLoadedImageFacts -Manifest $Manifest -LoadedReferences $loadedReferences -InspectedImages $inspectedImages
}

function Get-KanyikanInspectedImages {
    param([Parameter(Mandatory = $true)][psobject]$Manifest)

    $inspectedImages = @()
    foreach ($imageName in @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless')) {
        $reference = [string]$Manifest.images.$imageName.reference
        $inspectResult = Invoke-KanyikanDockerCommand -Arguments @(
            'image', 'inspect', $reference, '--format', '{{json .}}'
        )
        if ($inspectResult.exitCode -ne 0) { throw "无法检查已加载镜像：$imageName" }
        try { $inspect = $inspectResult.output | ConvertFrom-Json }
        catch { throw "Docker 返回了无法解析的镜像信息：$imageName" }
        $inspectedImages += [pscustomobject]@{
            reference = $reference
            imageId = [string]$inspect.Id
            os = [string]$inspect.Os
            architecture = [string]$inspect.Architecture
            repoDigests = @($inspect.RepoDigests)
        }
    }
    return $inspectedImages
}

function Test-KanyikanReleaseImagesPresent {
    param([Parameter(Mandatory = $true)][psobject]$Manifest)
    $references = @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless') | ForEach-Object { [string]$Manifest.images.$_.reference }
    return Test-KanyikanLoadedImageFacts -Manifest $Manifest -LoadedReferences $references -InspectedImages @(Get-KanyikanInspectedImages -Manifest $Manifest)
}

function ConvertFrom-KanyikanSecureString {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString]$Value
    )

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Test-KanyikanAdminPassword {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString]$Password,

        [Parameter(Mandatory = $true)]
        [Security.SecureString]$Confirmation
    )

    $passwordText = ConvertFrom-KanyikanSecureString -Value $Password
    $confirmationText = ConvertFrom-KanyikanSecureString -Value $Confirmation
    try {
        if ($passwordText -cne $confirmationText) {
            return [pscustomobject]@{ passed = $false; reason = '两次输入的管理员密码不一致。' }
        }
        if ($passwordText.Length -lt 16 -or @('admin123', 'password', 'changeme') -contains $passwordText.ToLowerInvariant()) {
            return [pscustomobject]@{ passed = $false; reason = '管理员密码必须至少为 16 个字符且不得使用默认口令。' }
        }
        if ($passwordText.IndexOf([char]0) -ge 0 -or $passwordText.Contains("`r") -or $passwordText.Contains("`n")) {
            return [pscustomobject]@{ passed = $false; reason = '管理员密码不得包含换行符或 NUL。' }
        }
        return [pscustomobject]@{ passed = $true; reason = $null }
    }
    finally {
        $passwordText = $null
        $confirmationText = $null
    }
}

function Read-KanyikanAdminPassword {
    $password = Read-Host '请输入管理员密码（至少 16 个字符）' -AsSecureString
    $confirmation = Read-Host '请再次输入管理员密码' -AsSecureString
    $result = Test-KanyikanAdminPassword -Password $password -Confirmation $confirmation
    if (-not $result.passed) { throw $result.reason }
    return $password
}

function New-KanyikanRandomBytes {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 4096)]
        [int]$Length
    )

    $bytes = New-Object byte[] $Length
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes); return $bytes }
    finally { $generator.Dispose() }
}

function ConvertTo-KanyikanBase64Url {
    param(
        [Parameter(Mandatory = $true)]
        [byte[]]$Bytes,

        [switch]$KeepPadding
    )

    $encoded = [Convert]::ToBase64String($Bytes).Replace('+', '-').Replace('/', '_')
    if (-not $KeepPadding) { $encoded = $encoded.TrimEnd('=') }
    return $encoded
}

function ConvertTo-KanyikanEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    if ($Value.IndexOf([char]0) -ge 0 -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw '环境变量值不得包含换行符或 NUL。'
    }
    $escaped = $Value.Replace('\', '\\').Replace('"', '\"').Replace('$', '$$')
    return '"' + $escaped + '"'
}

function Set-KanyikanRestrictedFileAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentUserSid = $currentIdentity.User
    $administratorsSid = New-Object Security.Principal.SecurityIdentifier(
        [Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid,
        $null
    )
    $security = New-Object Security.AccessControl.FileSecurity
    $security.SetOwner($currentUserSid)
    $security.SetAccessRuleProtection($true, $false)
    $rights = [Security.AccessControl.FileSystemRights]::FullControl
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $security.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($currentUserSid, $rights, $allow)))
    $security.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($administratorsSid, $rights, $allow)))
    [System.IO.File]::SetAccessControl($Path, $security)
}

function Test-KanyikanRestrictedFileAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not [System.IO.File]::Exists($Path)) { return $false }
    $security = [System.IO.File]::GetAccessControl($Path)
    if (-not $security.AreAccessRulesProtected) { return $false }
    $currentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $administratorsSid = (New-Object Security.Principal.SecurityIdentifier(
        [Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid,
        $null
    )).Value
    $rules = @($security.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { return $false }
    foreach ($rule in $rules) {
        if (@($currentUserSid, $administratorsSid) -cnotcontains $rule.IdentityReference.Value) { return $false }
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) { return $false }
        if (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl) { return $false }
    }
    return $true
}

function Get-KanyikanEnvironmentMap {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $values = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($Path, [Text.Encoding]::UTF8)) {
        if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
    }
    return $values
}

function Write-KanyikanSystemEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TemplatePath,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath,

        [Parameter(Mandatory = $true)]
        [psobject]$Manifest,

        [Parameter(Mandatory = $true)]
        [Security.SecureString]$AdminPassword
    )

    if ([System.IO.File]::Exists($DestinationPath)) { throw 'system.env 已存在；安装器拒绝覆盖现有密钥。' }
    if (-not [System.IO.File]::Exists($TemplatePath)) { throw '缺少 system.env.template。' }
    $adminPasswordText = ConvertFrom-KanyikanSecureString -Value $AdminPassword
    try {
        $passwordCheck = Test-KanyikanAdminPassword -Password $AdminPassword -Confirmation $AdminPassword
        if (-not $passwordCheck.passed) { throw $passwordCheck.reason }
        $secretKey = ConvertTo-KanyikanBase64Url -Bytes (New-KanyikanRandomBytes -Length 48)
        $encryptionKey = ConvertTo-KanyikanBase64Url -Bytes (New-KanyikanRandomBytes -Length 32) -KeepPadding
        $postgresPassword = ConvertTo-KanyikanBase64Url -Bytes (New-KanyikanRandomBytes -Length 32)
        $redisPassword = ConvertTo-KanyikanBase64Url -Bytes (New-KanyikanRandomBytes -Length 32)
        $browserlessToken = ConvertTo-KanyikanBase64Url -Bytes (New-KanyikanRandomBytes -Length 32)
        $postgresUrlPassword = [Uri]::EscapeDataString($postgresPassword)
        $redisUrlPassword = [Uri]::EscapeDataString($redisPassword)
        $overrides = [ordered]@{
            BACKEND_IMAGE = [string]$Manifest.images.backend.reference
            FRONTEND_IMAGE = [string]$Manifest.images.frontend.reference
            POSTGRES_IMAGE = [string]$Manifest.images.postgres.reference
            REDIS_IMAGE = [string]$Manifest.images.redis.reference
            NGINX_IMAGE = [string]$Manifest.images.nginx.reference
            BROWSERLESS_IMAGE = [string]$Manifest.images.browserless.reference
            SECRET_KEY = $secretKey
            CONFIG_ENCRYPTION_KEY = $encryptionKey
            ADMIN_PASSWORD = $adminPasswordText
            POSTGRES_PASSWORD = $postgresPassword
            REDIS_PASSWORD = $redisPassword
            BROWSERLESS_TOKEN = $browserlessToken
            DATABASE_URL = "postgresql://demand_user:$postgresUrlPassword@postgres:5432/demand_analyzer"
            REDIS_URL = "redis://:$redisUrlPassword@redis:6379/0"
        }

        $seen = @{}
        $outputLines = @()
        foreach ($line in [System.IO.File]::ReadAllLines($TemplatePath, [Text.Encoding]::UTF8)) {
            if ($line -match '^([A-Z][A-Z0-9_]*)=.*$' -and $overrides.Contains($Matches[1])) {
                $key = $Matches[1]
                $outputLines += "$key=$(ConvertTo-KanyikanEnvValue -Value ([string]$overrides[$key]))"
                $seen[$key] = $true
            }
            else { $outputLines += $line }
        }
        foreach ($key in $overrides.Keys) { if (-not $seen.ContainsKey($key)) { throw "system.env.template 缺少键 $key。" } }

        $directory = [System.IO.Path]::GetDirectoryName((Get-KanyikanNormalizedPath -Path $DestinationPath))
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        $temporaryPath = [System.IO.Path]::Combine($directory, ".system.env.$([Guid]::NewGuid().ToString('N')).tmp")
        try {
            $utf8WithoutBom = New-Object Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllLines($temporaryPath, $outputLines, $utf8WithoutBom)
            Set-KanyikanRestrictedFileAcl -Path $temporaryPath
            if (-not (Test-KanyikanRestrictedFileAcl -Path $temporaryPath)) { throw '无法收紧 system.env ACL。' }
            if (-not [KanyikanNativeMethods]::MoveFileEx($temporaryPath, $DestinationPath, (0x1 -bor 0x8))) {
                throw (New-Object ComponentModel.Win32Exception([Runtime.InteropServices.Marshal]::GetLastWin32Error()))
            }
        }
        finally {
            if ([System.IO.File]::Exists($temporaryPath)) { [System.IO.File]::Delete($temporaryPath) }
        }
    }
    finally {
        $adminPasswordText = $null
    }
}

function Test-KanyikanSystemEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [psobject]$Manifest
    )

    if (-not [System.IO.File]::Exists($Path) -or -not (Test-KanyikanRestrictedFileAcl -Path $Path)) { return $false }
    $values = Get-KanyikanEnvironmentMap -Path $Path
    $required = @('SECRET_KEY', 'CONFIG_ENCRYPTION_KEY', 'ADMIN_PASSWORD', 'POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'BROWSERLESS_TOKEN', 'DATABASE_URL', 'REDIS_URL')
    foreach ($key in $required) { if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace([string]$values[$key])) { return $false } }
    foreach ($imageName in @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless')) {
        $key = "$($imageName.ToUpperInvariant())_IMAGE"
        if (-not $values.ContainsKey($key) -or [string]$values[$key] -cne (ConvertTo-KanyikanEnvValue -Value ([string]$Manifest.images.$imageName.reference))) { return $false }
    }
    return $true
}

function Get-KanyikanCertificateDockerArguments {
    param(
        [Parameter(Mandatory = $true)][string]$BackendImage,
        [Parameter(Mandatory = $true)][string]$CertificateDirectory,
        [Parameter(Mandatory = $true)][ValidateRange(1, 825)][int]$LeafValidityDays,
        [Parameter(Mandatory = $true)][ValidateRange(825, 3650)][int]$CaValidityDays,
        [switch]$Validate
    )

    if ($BackendImage -notmatch '@sha256:[0-9a-f]{64}$') { throw '证书工具必须使用固定到 SHA256 digest 的 Backend 镜像。' }
    if ($CaValidityDays -le $LeafValidityDays) { throw '根 CA 有效期必须晚于叶子证书有效期。' }
    $directory = Get-KanyikanNormalizedPath -Path $CertificateDirectory
    $mountMode = if ($Validate) { 'ro' } else { 'rw' }
    $arguments = @(
        'run', '--rm', '--pull', 'never', '--platform', 'linux/amd64',
        '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
        '--volume', "${directory}:/certs:$mountMode",
        '--entrypoint', 'python', $BackendImage,
        '-m', 'app.tools.generate_local_certificate', '--output-dir', '/certs',
        '--leaf-validity-days', [string]$LeafValidityDays,
        '--ca-validity-days', [string]$CaValidityDays
    )
    if ($Validate) { $arguments += '--validate' }
    return $arguments
}

function Invoke-KanyikanCertificateTool {
    param(
        [Parameter(Mandatory = $true)][string]$BackendImage,
        [Parameter(Mandatory = $true)][string]$CertificateDirectory,
        [Parameter(Mandatory = $true)][int]$LeafValidityDays,
        [Parameter(Mandatory = $true)][int]$CaValidityDays,
        [switch]$Validate
    )

    $arguments = Get-KanyikanCertificateDockerArguments -BackendImage $BackendImage -CertificateDirectory $CertificateDirectory -LeafValidityDays $LeafValidityDays -CaValidityDays $CaValidityDays -Validate:$Validate
    $result = Invoke-KanyikanDockerCommand -Arguments $arguments
    if ($result.exitCode -ne 0) { throw "本地证书工具执行失败：$(Protect-KanyikanText -Text $result.output)" }
    $jsonLine = @($result.output -split '\r?\n' | Where-Object { $_.Trim().StartsWith('{') }) | Select-Object -Last 1
    if ([string]::IsNullOrWhiteSpace($jsonLine)) { throw '本地证书工具未返回可验证的公开元数据。' }
    try { return $jsonLine | ConvertFrom-Json }
    catch { throw '本地证书工具返回的公开元数据不是合法 JSON。' }
}

function Get-KanyikanLocalCaThumbprint {
    param([Parameter(Mandatory = $true)][string]$CertificatePath)
    $certificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2($CertificatePath)
    try { return $certificate.Thumbprint.ToUpperInvariant() }
    finally { $certificate.Dispose() }
}

function Test-KanyikanLocalCertificate {
    param(
        [Parameter(Mandatory = $true)][psobject]$Manifest,
        [Parameter(Mandatory = $true)][string]$CertificateDirectory
    )

    $directory = Get-KanyikanNormalizedPath -Path $CertificateDirectory
    $paths = @(
        [System.IO.Path]::Combine($directory, 'local-root-ca.crt'),
        [System.IO.Path]::Combine($directory, 'localhost.crt'),
        [System.IO.Path]::Combine($directory, 'localhost.key')
    )
    foreach ($path in $paths) {
        if (-not [System.IO.File]::Exists($path) -or -not (Test-KanyikanRestrictedFileAcl -Path $path)) { throw "证书材料缺失或 ACL 不合格：$([System.IO.Path]::GetFileName($path))" }
    }
    if ([System.IO.File]::Exists([System.IO.Path]::Combine($directory, 'local-root-ca.key'))) { throw '检测到不应保留的根 CA 私钥。' }
    $metadata = Invoke-KanyikanCertificateTool -BackendImage ([string]$Manifest.images.backend.reference) -CertificateDirectory $directory -LeafValidityDays ([int]$Manifest.tls.leafValidityDays) -CaValidityDays ([int]$Manifest.tls.caValidityDays) -Validate
    if ([string]$metadata.ca_sha256 -notmatch '^[0-9a-f]{64}$' -or [string]$metadata.leaf_sha256 -notmatch '^[0-9a-f]{64}$') { throw '证书公开摘要元数据不合法。' }
    return [pscustomobject][ordered]@{
        caThumbprint = Get-KanyikanLocalCaThumbprint -CertificatePath $paths[0]
        caSha256 = [string]$metadata.ca_sha256
        leafSha256 = [string]$metadata.leaf_sha256
        caNotAfter = [string]$metadata.ca_not_after
        leafNotAfter = [string]$metadata.leaf_not_after
    }
}

function New-KanyikanLocalCertificate {
    param(
        [Parameter(Mandatory = $true)][psobject]$Manifest,
        [Parameter(Mandatory = $true)][string]$CertificateDirectory
    )

    $directory = Get-KanyikanNormalizedPath -Path $CertificateDirectory
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $materialNames = @('local-root-ca.crt', 'localhost.crt', 'localhost.key')
    foreach ($name in $materialNames) {
        if ([System.IO.File]::Exists([System.IO.Path]::Combine($directory, $name))) { throw '证书材料已存在；请使用复核流程，安装器不会覆盖现有证书。' }
    }
    $generated = $false
    try {
        Invoke-KanyikanCertificateTool -BackendImage ([string]$Manifest.images.backend.reference) -CertificateDirectory $directory -LeafValidityDays ([int]$Manifest.tls.leafValidityDays) -CaValidityDays ([int]$Manifest.tls.caValidityDays) | Out-Null
        $generated = $true
        foreach ($name in $materialNames) {
            $path = [System.IO.Path]::Combine($directory, $name)
            if (-not [System.IO.File]::Exists($path)) { throw "证书工具缺少输出：$name" }
            Set-KanyikanRestrictedFileAcl -Path $path
        }
        return Test-KanyikanLocalCertificate -Manifest $Manifest -CertificateDirectory $directory
    }
    catch {
        if ($generated) {
            foreach ($name in $materialNames) {
                $path = [System.IO.Path]::Combine($directory, $name)
                if ([System.IO.File]::Exists($path)) { [System.IO.File]::Delete($path) }
            }
        }
        throw
    }
}

function Install-KanyikanLocalRootTrust {
    param(
        [Parameter(Mandatory = $true)][string]$CertificatePath,
        [Parameter(Mandatory = $true)][bool]$Consent
    )

    if (-not $Consent) { return $null }
    $certificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2($CertificatePath)
    $store = New-Object Security.Cryptography.X509Certificates.X509Store([Security.Cryptography.X509Certificates.StoreName]::Root, [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
    try {
        $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        if (@($store.Certificates | Where-Object { $_.Thumbprint -ceq $certificate.Thumbprint }).Count -eq 0) { $store.Add($certificate) }
        return $certificate.Thumbprint.ToUpperInvariant()
    }
    finally { $store.Close(); $certificate.Dispose() }
}

function Remove-KanyikanLocalRootTrust {
    param([Parameter(Mandatory = $true)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$Thumbprint)
    $normalizedThumbprint = $Thumbprint.ToUpperInvariant()
    $store = New-Object Security.Cryptography.X509Certificates.X509Store([Security.Cryptography.X509Certificates.StoreName]::Root, [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
    $removed = 0
    try {
        $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        foreach ($certificate in @($store.Certificates | Where-Object { $_.Thumbprint -ceq $normalizedThumbprint })) { $store.Remove($certificate); $removed++ }
    }
    finally { $store.Close() }
    return $removed
}

function Get-KanyikanComposeArguments {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    return @(
        'compose', '--project-name', $script:ComposeProjectName,
        '--env-file', [System.IO.Path]::Combine($root, 'config', 'system.env'),
        '--file', [System.IO.Path]::Combine($root, 'compose.release.yml')
    ) + $Arguments
}

function Invoke-KanyikanComposeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    return Invoke-KanyikanDockerCommand -Arguments (Get-KanyikanComposeArguments -InstallRoot $InstallRoot -Arguments $Arguments)
}

function Get-KanyikanServiceFacts {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $result = Invoke-KanyikanComposeCommand -InstallRoot $InstallRoot -Arguments @('ps', '--format', 'json', '--all')
    if ($result.exitCode -ne 0) { throw "无法读取 Compose 服务状态：$(Protect-KanyikanText -Text $result.output)" }
    if ([string]::IsNullOrWhiteSpace($result.output)) { return @() }
    try {
        if ($result.output.TrimStart().StartsWith('[')) { return @($result.output | ConvertFrom-Json) }
        $facts = @()
        foreach ($line in ($result.output -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) { $facts += $line | ConvertFrom-Json }
        return $facts
    }
    catch { throw 'Docker Compose 返回了无法解析的服务状态。' }
}

function Test-KanyikanServiceFacts {
    param([Parameter(Mandatory = $true)][psobject[]]$Facts)

    $expectedServices = @('postgres', 'redis', 'backend', 'worker', 'crawler', 'beat', 'outbox-relay', 'frontend', 'nginx', 'browserless')
    if ($Facts.Count -ne 10) { return [pscustomobject]@{ passed = $false; reason = "服务数量不是 10，实际为 $($Facts.Count)。" } }
    foreach ($service in $expectedServices) {
        $matches = @($Facts | Where-Object { [string]$_.Service -ceq $service })
        if ($matches.Count -ne 1) { return [pscustomobject]@{ passed = $false; reason = "服务 $service 缺失或重复。" } }
        $fact = $matches[0]
        if ([string]$fact.State -cne 'running' -or [string]$fact.Health -cne 'healthy') { return [pscustomobject]@{ passed = $false; reason = "服务 $service 尚未健康。" } }
        $publishers = @($fact.Publishers)
        if ($service -ceq 'nginx') {
            if ($publishers.Count -ne 1) { return [pscustomobject]@{ passed = $false; reason = 'Nginx 发布端口数量不合法。' } }
            $publisher = $publishers[0]
            if ([string]$publisher.URL -cne '127.0.0.1' -or [int]$publisher.PublishedPort -ne 10443 -or [int]$publisher.TargetPort -ne 443 -or [string]$publisher.Protocol -cne 'tcp') { return [pscustomobject]@{ passed = $false; reason = 'Nginx 未精确绑定 127.0.0.1:10443。' } }
        }
        elseif ($publishers.Count -ne 0) { return [pscustomobject]@{ passed = $false; reason = "服务 $service 不得发布宿主端口。" } }
    }
    return [pscustomobject]@{ passed = $true; reason = $null }
}

function Get-KanyikanLeafCertificateSha256Bytes {
    param([Parameter(Mandatory = $true)][string]$CertificatePath)
    $certificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2($CertificatePath)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try { return $sha256.ComputeHash($certificate.RawData) }
    finally { $sha256.Dispose(); $certificate.Dispose() }
}

function Test-KanyikanBootstrapEndpoints {
    param(
        [Parameter(Mandatory = $true)][string]$LeafCertificatePath,
        [int]$RequestTimeoutSeconds = 10
    )

    $expectedSha256 = Get-KanyikanLeafCertificateSha256Bytes -CertificatePath $LeafCertificatePath
    foreach ($path in @('/health', '/ready')) {
        try { $statusCode = [KanyikanPinnedHttps]::GetStatusCode("https://127.0.0.1:10443$path", $expectedSha256, ($RequestTimeoutSeconds * 1000)) }
        catch { return [pscustomobject]@{ passed = $false; reason = "$path 请求失败：$(Protect-KanyikanText -Text $_.Exception.Message)" } }
        if ($statusCode -lt 200 -or $statusCode -ge 300) { return [pscustomobject]@{ passed = $false; reason = "$path 返回 HTTP $statusCode。" } }
    }
    return [pscustomobject]@{ passed = $true; reason = $null }
}

function Wait-KanyikanBootstrapReady {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 600,
        [ValidateRange(1, 60)][int]$PollIntervalSeconds = 5
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastReason = '服务尚未报告状态。'
    do {
        try {
            $serviceResult = Test-KanyikanServiceFacts -Facts @(Get-KanyikanServiceFacts -InstallRoot $InstallRoot)
            if ($serviceResult.passed) {
                $endpointResult = Test-KanyikanBootstrapEndpoints -LeafCertificatePath ([System.IO.Path]::Combine((Get-KanyikanNormalizedPath -Path $InstallRoot), 'config', 'certs', 'localhost.crt'))
                if ($endpointResult.passed) { return [pscustomobject]@{ passed = $true; reason = $null } }
                $lastReason = $endpointResult.reason
            }
            else { $lastReason = $serviceResult.reason }
        }
        catch { $lastReason = Protect-KanyikanText -Text $_.Exception.Message }
        if ([DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds $PollIntervalSeconds }
    } while ([DateTime]::UtcNow -lt $deadline)
    return [pscustomobject]@{ passed = $false; reason = "健康检查超时：$lastReason" }
}

function Start-KanyikanServices {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)
    $config = Invoke-KanyikanComposeCommand -InstallRoot $InstallRoot -Arguments @('config', '--quiet')
    if ($config.exitCode -ne 0) { throw "Compose 配置无效：$(Protect-KanyikanText -Text $config.output)" }
    $up = Invoke-KanyikanComposeCommand -InstallRoot $InstallRoot -Arguments @('up', '--detach', '--no-build', '--pull', 'never', '--remove-orphans')
    if ($up.exitCode -ne 0) { throw "Compose 启动失败：$(Protect-KanyikanText -Text $up.output)" }
}

function Stop-KanyikanServices {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)
    $result = Invoke-KanyikanComposeCommand -InstallRoot $InstallRoot -Arguments @('stop')
    if ($result.exitCode -ne 0) { throw "Compose 停止失败：$(Protect-KanyikanText -Text $result.output)" }
}

Export-ModuleMember -Function @(
    'Get-KanyikanCertificateDockerArguments',
    'Get-KanyikanComposeArguments',
    'Get-KanyikanInstallStates',
    'Get-KanyikanLocalCaThumbprint',
    'Get-KanyikanStatePath',
    'Get-KanyikanHostFacts',
    'Get-KanyikanInspectedImages',
    'Get-KanyikanServiceFacts',
    'Import-KanyikanReleaseImages',
    'Install-KanyikanLocalRootTrust',
    'Invoke-KanyikanPreflight',
    'New-KanyikanInstallState',
    'New-KanyikanLocalCertificate',
    'Read-KanyikanAdminPassword',
    'Read-KanyikanInstallState',
    'Remove-KanyikanLocalRootTrust',
    'Set-KanyikanInstallState',
    'Set-KanyikanInstallFailure',
    'Start-KanyikanServices',
    'Stop-KanyikanServices',
    'Test-KanyikanAdminPassword',
    'Test-KanyikanPreflightFacts',
    'Test-KanyikanLoadedImageFacts',
    'Test-KanyikanLocalCertificate',
    'Test-KanyikanReleasePackage',
    'Test-KanyikanReleaseImagesPresent',
    'Test-KanyikanRestrictedFileAcl',
    'Test-KanyikanSystemEnvironment',
    'Test-KanyikanServiceFacts',
    'Wait-KanyikanBootstrapReady',
    'Write-KanyikanSystemEnvironment'
)
