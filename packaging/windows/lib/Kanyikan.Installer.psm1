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

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CreateHardLink(
        string fileName,
        string existingFileName,
        IntPtr securityAttributes
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
        caTrusted = $false
        installationActive = $false
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
        '(?i)\b(SECRET_KEY|CONFIG_ENCRYPTION_KEY|ADMIN_PASSWORD|POSTGRES_PASSWORD|REDIS_PASSWORD|BROWSERLESS_TOKEN|PASSWORD|API_KEY|COOKIE|JWT|SENTRY_DSN)\s*[:=]\s*([^\s;,]+)',
        '(?i)(https?://)[^\s:/@]+:[^\s/@]+@'
    )
    $replacements = @(
        '[REDACTED]',
        '${1}[REDACTED]',
        '${1}=[REDACTED]',
        '${1}[REDACTED]@'
    )

    for ($index = 0; $index -lt $patterns.Count; $index++) {
        $protected = [regex]::Replace($protected, $patterns[$index], $replacements[$index])
    }
    return $protected
}

function New-KanyikanLogFile {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)
    $logDirectory = [System.IO.Path]::Combine((Get-KanyikanNormalizedPath -Path $InstallRoot), 'logs')
    [System.IO.Directory]::CreateDirectory($logDirectory) | Out-Null
    Set-KanyikanRestrictedDirectoryAcl -Path $logDirectory
    $path = [System.IO.Path]::Combine($logDirectory, "kanyikan-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([Guid]::NewGuid().ToString('N').Substring(0, 8)).log")
    [System.IO.File]::WriteAllText($path, '', (New-Object Text.UTF8Encoding($false)))
    Set-KanyikanRestrictedFileAcl -Path $path
    return $path
}

function Write-KanyikanLog {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Level,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Message,
        [int]$ExitCode = 0
    )
    $record = [pscustomobject][ordered]@{
        timestamp = [DateTime]::UtcNow.ToString('o')
        level = Protect-KanyikanText -Text $Level
        command = Protect-KanyikanText -Text $Command
        stage = Protect-KanyikanText -Text $Stage
        exitCode = $ExitCode
        message = Protect-KanyikanText -Text $Message
    }
    $line = $record | ConvertTo-Json -Compress
    if (-not (Test-KanyikanSupportPayload -Text $line)) { throw '安装器日志敏感信息扫描失败。' }
    [System.IO.File]::AppendAllText($Path, $line + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
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
        'caTrusted',
        'installationActive',
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
    if ($NextState -ceq 'INSTALLED') { $State.installationActive = $true }
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

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & docker.exe @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return [pscustomobject]@{
        exitCode = $exitCode
        output = (@($output) -join [Environment]::NewLine).Trim()
    }
}

function Get-KanyikanHostFacts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot,

        [switch]$ReadOnly
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
        if ($ReadOnly) { throw [OperationCanceledException]::new('只读诊断不执行写入探测') }
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
        installRootWritable = if ($ReadOnly) { $null } else { $installRootWritable }
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

function Assert-KanyikanReleaseAssetPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-KanyikanPackageRelativePath -Path $Path
    if ($Path -match '^(?i:(?:state|data|logs|support)(?:/|$)|config/(?:system\.env$|certs(?:/|$)))') {
        throw "发行文件不得覆盖安装器生成路径：$Path"
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
        'tls', 'compose', 'resources', 'files', 'images', 'upgrade', 'signing'
    ) -Context 'manifest'
    if ($Manifest.schemaVersion -ne 1 -or $Manifest.product -cne 'Kanyikan') { throw 'manifest 产品或契约版本不合法。' }

    Assert-KanyikanExactProperties -Value $Manifest.release -Names @('version', 'publishedAt', 'sourceCommit', 'packageType') -Context 'release'
    if ([string]$Manifest.release.version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$') { throw 'release.version 不是合法语义化版本。' }
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
        Assert-KanyikanReleaseAssetPath -Path ([string]$file.path)
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

    Assert-KanyikanExactProperties -Value $Manifest.upgrade -Names @('supportedFrom', 'migration', 'smokeTests') -Context 'upgrade'
    $seenSourceVersions = @{}
    foreach ($sourceVersion in @($Manifest.upgrade.supportedFrom)) {
        if ([string]$sourceVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$') { throw "upgrade.supportedFrom 包含非法版本：$sourceVersion" }
        if ($seenSourceVersions.ContainsKey([string]$sourceVersion)) { throw "upgrade.supportedFrom 包含重复版本：$sourceVersion" }
        $seenSourceVersions[[string]$sourceVersion] = $true
    }
    Assert-KanyikanExactProperties -Value $Manifest.upgrade.migration -Names @('strategy', 'requiresFullBackup', 'rollbackStrategy') -Context 'upgrade.migration'
    if (@('none', 'alembic_upgrade_head') -cnotcontains [string]$Manifest.upgrade.migration.strategy -or $Manifest.upgrade.migration.requiresFullBackup -ne $true -or $Manifest.upgrade.migration.rollbackStrategy -cne 'restore_full_backup') { throw '升级迁移或回滚策略不合法。' }
    $expectedSmokeTests = @('https_health', 'https_ready', 'admin_login', 'core_api')
    $actualSmokeTests = @($Manifest.upgrade.smokeTests)
    if ($actualSmokeTests.Count -ne $expectedSmokeTests.Count) { throw '升级冒烟检查契约不合法。' }
    for ($index = 0; $index -lt $expectedSmokeTests.Count; $index++) {
        if ([string]$actualSmokeTests[$index] -cne $expectedSmokeTests[$index]) { throw '升级冒烟检查契约不合法。' }
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

function ConvertTo-KanyikanSemanticVersionParts {
    param([Parameter(Mandatory = $true)][string]$Version)

    $pattern = '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$'
    if ($Version -notmatch $pattern) { throw "不是合法的语义化版本：$Version" }
    return [pscustomobject]@{
        core = @(
            [System.Numerics.BigInteger]::Parse($Matches[1]),
            [System.Numerics.BigInteger]::Parse($Matches[2]),
            [System.Numerics.BigInteger]::Parse($Matches[3])
        )
        prerelease = if ([string]::IsNullOrEmpty($Matches[4])) { @() } else { @($Matches[4].Split('.')) }
    }
}

function Compare-KanyikanSemanticVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )

    $leftParts = ConvertTo-KanyikanSemanticVersionParts -Version $Left
    $rightParts = ConvertTo-KanyikanSemanticVersionParts -Version $Right
    for ($index = 0; $index -lt 3; $index++) {
        $comparison = $leftParts.core[$index].CompareTo($rightParts.core[$index])
        if ($comparison -ne 0) { return $comparison }
    }
    $leftPre = @($leftParts.prerelease)
    $rightPre = @($rightParts.prerelease)
    if ($leftPre.Count -eq 0 -and $rightPre.Count -eq 0) { return 0 }
    if ($leftPre.Count -eq 0) { return 1 }
    if ($rightPre.Count -eq 0) { return -1 }
    $count = [Math]::Min($leftPre.Count, $rightPre.Count)
    for ($index = 0; $index -lt $count; $index++) {
        $leftNumeric = $leftPre[$index] -match '^[0-9]+$'
        $rightNumeric = $rightPre[$index] -match '^[0-9]+$'
        if ($leftNumeric -and $rightNumeric) {
            $comparison = [System.Numerics.BigInteger]::Parse($leftPre[$index]).CompareTo([System.Numerics.BigInteger]::Parse($rightPre[$index]))
        }
        elseif ($leftNumeric) { $comparison = -1 }
        elseif ($rightNumeric) { $comparison = 1 }
        else { $comparison = [StringComparer]::Ordinal.Compare($leftPre[$index], $rightPre[$index]) }
        if ($comparison -ne 0) { return $comparison }
    }
    return $leftPre.Count.CompareTo($rightPre.Count)
}

function Test-KanyikanUpgradePath {
    param(
        [Parameter(Mandatory = $true)][string]$CurrentVersion,
        [Parameter(Mandatory = $true)][psobject]$Manifest
    )

    $targetVersion = [string]$Manifest.release.version
    if ((Compare-KanyikanSemanticVersion -Left $targetVersion -Right $CurrentVersion) -le 0) {
        throw "更新版本必须严格高于当前版本：$CurrentVersion -> $targetVersion"
    }
    if (@($Manifest.upgrade.supportedFrom) -cnotcontains $CurrentVersion) {
        throw "更新包不支持从当前版本直接升级：$CurrentVersion -> $targetVersion"
    }
    return [pscustomobject][ordered]@{
        currentVersion = $CurrentVersion
        targetVersion = $targetVersion
        migrationStrategy = [string]$Manifest.upgrade.migration.strategy
    }
}

function Expand-KanyikanUpdatePackage {
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    $archivePath = Get-KanyikanNormalizedPath -Path $ZipPath
    $destination = Get-KanyikanNormalizedPath -Path $DestinationRoot
    if (-not [System.IO.File]::Exists($archivePath)) { throw "更新包不存在：$archivePath" }
    if ([System.IO.Directory]::Exists($destination) -or [System.IO.File]::Exists($destination)) { throw "更新暂存目录已存在：$destination" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = $null
    $createdDestination = $false
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
        if ($archive.Entries.Count -lt 1 -or $archive.Entries.Count -gt 20000) { throw '更新 ZIP 文件项数量不合法。' }
        $seenEntries = @{}
        $topLevel = $null
        $totalLength = 0L
        foreach ($entry in $archive.Entries) {
            $entryName = [string]$entry.FullName
            if ([string]::IsNullOrWhiteSpace($entryName) -or $entryName.Contains('\')) { throw "更新 ZIP 包含非法路径：$entryName" }
            $trimmedName = $entryName.TrimEnd('/')
            Assert-KanyikanPackageRelativePath -Path $trimmedName
            if ($seenEntries.ContainsKey($trimmedName)) { throw "更新 ZIP 包含重复路径：$trimmedName" }
            $seenEntries[$trimmedName] = $true
            $segments = @($trimmedName.Split('/'))
            if ([string]::IsNullOrEmpty($topLevel)) { $topLevel = $segments[0] }
            elseif ($segments[0] -cne $topLevel) { throw '更新 ZIP 必须只包含一个顶层发行目录。' }
            if (-not $entryName.EndsWith('/') -and $segments.Count -lt 2) { throw '更新 ZIP 的文件必须位于唯一顶层发行目录内。' }
            if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw "更新 ZIP 不得包含符号链接：$entryName" }
            $totalLength += [int64]$entry.Length
            if ($totalLength -gt 107374182400L) { throw '更新 ZIP 解压后大小超过 100 GiB 限制。' }
        }

        [System.IO.Directory]::CreateDirectory($destination) | Out-Null
        $createdDestination = $true
        $destinationPrefix = $destination + [System.IO.Path]::DirectorySeparatorChar
        foreach ($entry in $archive.Entries) {
            $relativePath = ([string]$entry.FullName).Replace('/', [System.IO.Path]::DirectorySeparatorChar)
            $targetPath = Get-KanyikanNormalizedPath -Path ([System.IO.Path]::Combine($destination, $relativePath))
            if (-not $targetPath.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "更新 ZIP 路径越界：$($entry.FullName)" }
            if (([string]$entry.FullName).EndsWith('/')) {
                [System.IO.Directory]::CreateDirectory($targetPath) | Out-Null
                continue
            }
            [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($targetPath)) | Out-Null
            $inputStream = $entry.Open()
            $outputStream = New-Object System.IO.FileStream($targetPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try { $inputStream.CopyTo($outputStream) }
            finally { $outputStream.Dispose(); $inputStream.Dispose() }
        }
        return [System.IO.Path]::Combine($destination, $topLevel)
    }
    catch {
        if ($createdDestination -and [System.IO.Directory]::Exists($destination)) { [System.IO.Directory]::Delete($destination, $true) }
        throw
    }
    finally {
        if ($null -ne $archive) { $archive.Dispose() }
    }
}

function Get-KanyikanReleaseAssetRelativePaths {
    param([Parameter(Mandatory = $true)][psobject]$Manifest)

    $paths = @(@($Manifest.files) | ForEach-Object { [string]$_.path })
    $paths += @('release-manifest.json', 'release-manifest.sig', 'manifest.sha256')
    $seen = @{}
    foreach ($path in $paths) {
        Assert-KanyikanReleaseAssetPath -Path $path
        if ($seen.ContainsKey($path)) { throw "发行资产路径重复：$path" }
        $seen[$path] = $true
    }
    return @($paths)
}

function Assert-KanyikanUpdateTransactionPath {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    $transactionRoot = [System.IO.Path]::Combine($root, 'state', 'update-transactions')
    $candidate = Get-KanyikanNormalizedPath -Path $Path
    $prefix = $transactionRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw '更新资产快照必须位于 state/update-transactions 内。' }
    return $candidate
}

function New-KanyikanHardLinkOrCopy {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($DestinationPath)) | Out-Null
    if (-not [KanyikanNativeMethods]::CreateHardLink($DestinationPath, $SourcePath, [IntPtr]::Zero)) {
        [System.IO.File]::Copy($SourcePath, $DestinationPath, $false)
    }
}

function New-KanyikanReleaseAssetSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$CurrentManifest,
        [Parameter(Mandatory = $true)][string]$SnapshotRoot
    )

    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    $snapshot = Assert-KanyikanUpdateTransactionPath -InstallRoot $root -Path $SnapshotRoot
    if ([System.IO.Directory]::Exists($snapshot) -or [System.IO.File]::Exists($snapshot)) { throw "更新资产快照已存在：$snapshot" }
    $paths = @(Get-KanyikanReleaseAssetRelativePaths -Manifest $CurrentManifest)
    foreach ($relativePath in $paths) {
        $sourcePath = [System.IO.Path]::Combine($root, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        if (-not [System.IO.File]::Exists($sourcePath)) { throw "当前发行资产缺失：$relativePath" }
        if (([System.IO.File]::GetAttributes($sourcePath) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "当前发行资产不得是重解析点：$relativePath" }
    }

    [System.IO.Directory]::CreateDirectory($snapshot) | Out-Null
    try {
        foreach ($relativePath in $paths) {
            $sourcePath = [System.IO.Path]::Combine($root, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
            $snapshotPath = [System.IO.Path]::Combine($snapshot, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
            New-KanyikanHardLinkOrCopy -SourcePath $sourcePath -DestinationPath $snapshotPath
        }
        return $snapshot
    }
    catch {
        if ([System.IO.Directory]::Exists($snapshot)) { [System.IO.Directory]::Delete($snapshot, $true) }
        throw
    }
}

function Set-KanyikanReleaseAssets {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$PackageRoot,
        [Parameter(Mandatory = $true)][psobject]$CurrentManifest,
        [Parameter(Mandatory = $true)][psobject]$NewManifest
    )

    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    $package = Get-KanyikanNormalizedPath -Path $PackageRoot
    $newPaths = @(Get-KanyikanReleaseAssetRelativePaths -Manifest $NewManifest)
    $currentPaths = @(Get-KanyikanReleaseAssetRelativePaths -Manifest $CurrentManifest)
    foreach ($relativePath in $newPaths) {
        $sourcePath = [System.IO.Path]::Combine($package, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        if (-not [System.IO.File]::Exists($sourcePath)) { throw "新发行资产缺失：$relativePath" }
        if (([System.IO.File]::GetAttributes($sourcePath) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "新发行资产不得是重解析点：$relativePath" }
    }

    foreach ($relativePath in $newPaths) {
        $sourcePath = [System.IO.Path]::Combine($package, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        $destinationPath = [System.IO.Path]::Combine($root, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($destinationPath)) | Out-Null
        $temporaryPath = "$destinationPath.update.$([Guid]::NewGuid().ToString('N')).tmp"
        try {
            New-KanyikanHardLinkOrCopy -SourcePath $sourcePath -DestinationPath $temporaryPath
            if (-not [KanyikanNativeMethods]::MoveFileEx($temporaryPath, $destinationPath, (0x1 -bor 0x8))) {
                throw (New-Object ComponentModel.Win32Exception([Runtime.InteropServices.Marshal]::GetLastWin32Error()))
            }
        }
        finally { if ([System.IO.File]::Exists($temporaryPath)) { [System.IO.File]::Delete($temporaryPath) } }
    }
    foreach ($relativePath in $currentPaths) {
        if ($newPaths -cnotcontains $relativePath) {
            $obsoletePath = [System.IO.Path]::Combine($root, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
            if ([System.IO.File]::Exists($obsoletePath)) { [System.IO.File]::Delete($obsoletePath) }
        }
    }
}

function Restore-KanyikanReleaseAssets {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$SnapshotRoot,
        [Parameter(Mandatory = $true)][psobject]$PreviousManifest,
        [Parameter(Mandatory = $true)][psobject]$FailedManifest
    )

    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    $snapshot = Assert-KanyikanUpdateTransactionPath -InstallRoot $root -Path $SnapshotRoot
    if (-not [System.IO.Directory]::Exists($snapshot)) { throw '更新资产快照不存在。' }
    $previousPaths = @(Get-KanyikanReleaseAssetRelativePaths -Manifest $PreviousManifest)
    $failedPaths = @(Get-KanyikanReleaseAssetRelativePaths -Manifest $FailedManifest)
    foreach ($relativePath in $previousPaths) {
        $snapshotPath = [System.IO.Path]::Combine($snapshot, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        if (-not [System.IO.File]::Exists($snapshotPath)) { throw "更新资产快照缺失：$relativePath" }
    }
    foreach ($relativePath in $previousPaths) {
        $snapshotPath = [System.IO.Path]::Combine($snapshot, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        $destinationPath = [System.IO.Path]::Combine($root, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($destinationPath)) | Out-Null
        $temporaryPath = "$destinationPath.rollback.$([Guid]::NewGuid().ToString('N')).tmp"
        try {
            New-KanyikanHardLinkOrCopy -SourcePath $snapshotPath -DestinationPath $temporaryPath
            if (-not [KanyikanNativeMethods]::MoveFileEx($temporaryPath, $destinationPath, (0x1 -bor 0x8))) {
                throw (New-Object ComponentModel.Win32Exception([Runtime.InteropServices.Marshal]::GetLastWin32Error()))
            }
        }
        finally { if ([System.IO.File]::Exists($temporaryPath)) { [System.IO.File]::Delete($temporaryPath) } }
    }
    foreach ($relativePath in $failedPaths) {
        if ($previousPaths -cnotcontains $relativePath) {
            $failedPath = [System.IO.Path]::Combine($root, $relativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
            if ([System.IO.File]::Exists($failedPath)) { [System.IO.File]::Delete($failedPath) }
        }
    }
}

function Remove-KanyikanReleaseAssetSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$SnapshotRoot
    )

    $snapshot = Assert-KanyikanUpdateTransactionPath -InstallRoot $InstallRoot -Path $SnapshotRoot
    if ([System.IO.Directory]::Exists($snapshot)) {
        Assert-KanyikanSafeRemovalTree -InstallRoot $InstallRoot -Path $snapshot
        [System.IO.Directory]::Delete($snapshot, $true)
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

function Set-KanyikanRestrictedDirectoryAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $currentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $administratorsSid = New-Object Security.Principal.SecurityIdentifier([Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
    $security = New-Object Security.AccessControl.DirectorySecurity
    $security.SetOwner($currentUserSid)
    $security.SetAccessRuleProtection($true, $false)
    $rights = [Security.AccessControl.FileSystemRights]::FullControl
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $security.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($currentUserSid, $rights, $inheritance, $propagation, $allow)))
    $security.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($administratorsSid, $rights, $inheritance, $propagation, $allow)))
    [System.IO.Directory]::SetAccessControl($Path, $security)
}

function Set-KanyikanBackupAcl {
    param([Parameter(Mandatory = $true)][string]$BackupDirectory)
    $root = Get-KanyikanNormalizedPath -Path $BackupDirectory
    foreach ($directory in @($root) + @([System.IO.Directory]::GetDirectories($root, '*', [System.IO.SearchOption]::AllDirectories))) { Set-KanyikanRestrictedDirectoryAcl -Path $directory }
    foreach ($file in [System.IO.Directory]::GetFiles($root, '*', [System.IO.SearchOption]::AllDirectories)) { Set-KanyikanRestrictedFileAcl -Path $file }
}

function Get-KanyikanBackupArguments {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$State,
        [string]$BackupName
    )

    if ([string]::IsNullOrWhiteSpace($BackupName)) {
        $composeOperation = @('exec', '--no-TTY', 'backend', 'python', '-m', 'app.tools.local_backup')
        $toolArguments = @(
            'create', '--backup-root', '/backups',
            '--snapshots-root', '/app/data/snapshots',
            '--skills-root', '/app/data/workspace_skills',
            '--product-version', [string]$State.productVersion,
            '--manifest-sha256', [string]$State.manifestSha256,
            '--release-public-key-sha256', [string]$State.releasePublicKeySha256
        )
    }
    else {
        if ($BackupName -notmatch '^kanyikan-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$') { throw '备份名称不合法。' }
        $composeOperation = @('run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint', 'python', 'backend', '-m', 'app.tools.local_backup')
        $toolArguments = @('validate', '--backup-root', '/backups', '--backup', "/backups/$BackupName")
    }
    $composeArguments = @(Get-KanyikanComposeArguments -InstallRoot $InstallRoot -Arguments $composeOperation)
    return $composeArguments + $toolArguments
}

function Invoke-KanyikanBackup {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$State
    )

    $result = Invoke-KanyikanDockerCommand -Arguments (Get-KanyikanBackupArguments -InstallRoot $InstallRoot -State $State)
    if ($result.exitCode -ne 0) { throw "完整备份失败：$(Protect-KanyikanText -Text $result.output)" }
    $jsonLine = @($result.output -split '\r?\n' | Where-Object { $_.Trim().StartsWith('{') }) | Select-Object -Last 1
    try { $metadata = $jsonLine | ConvertFrom-Json }
    catch { throw '完整备份工具未返回合法公开元数据。' }
    $backupName = [string]$metadata.backup
    if ($backupName -notmatch '^kanyikan-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$') { throw '完整备份工具返回了非法备份名称。' }

    $verify = Invoke-KanyikanDockerCommand -Arguments (Get-KanyikanBackupArguments -InstallRoot $InstallRoot -State $State -BackupName $backupName)
    if ($verify.exitCode -ne 0) { throw "完整备份最终校验失败：$(Protect-KanyikanText -Text $verify.output)" }
    $windowsPath = [System.IO.Path]::Combine((Get-KanyikanNormalizedPath -Path $InstallRoot), 'data', 'backups', $backupName)
    if (-not [System.IO.Directory]::Exists($windowsPath)) { throw '完整备份未出现在安装目录 data/backups。' }
    Set-KanyikanBackupAcl -BackupDirectory $windowsPath
    return [pscustomobject][ordered]@{ name = $backupName; path = $windowsPath; valid = $true }
}

function Resolve-KanyikanBackupDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$BackupPath
    )
    $backupRoot = Get-KanyikanNormalizedPath -Path ([System.IO.Path]::Combine((Get-KanyikanNormalizedPath -Path $InstallRoot), 'data', 'backups'))
    $resolved = Get-KanyikanNormalizedPath -Path $BackupPath
    $prefix = $backupRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or -not [System.IO.Directory]::Exists($resolved)) { throw '恢复目录必须存在于 data/backups 内。' }
    if ([System.IO.Path]::GetFileName($resolved) -notmatch '^kanyikan-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$') { throw '恢复目录名称不合法。' }
    $cursor = $resolved
    while ($cursor.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase)) {
        if (([System.IO.File]::GetAttributes($cursor) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw '恢复路径不得包含重解析点。' }
        if ([string]::Equals($cursor, $backupRoot, [StringComparison]::OrdinalIgnoreCase)) { break }
        $cursor = [System.IO.Directory]::GetParent($cursor).FullName
    }
    return $resolved
}

function Invoke-KanyikanValidateBackup {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$State,
        [Parameter(Mandatory = $true)][string]$BackupPath
    )
    $resolved = Resolve-KanyikanBackupDirectory -InstallRoot $InstallRoot -BackupPath $BackupPath
    $name = [System.IO.Path]::GetFileName($resolved)
    $result = Invoke-KanyikanDockerCommand -Arguments (Get-KanyikanBackupArguments -InstallRoot $InstallRoot -State $State -BackupName $name)
    if ($result.exitCode -ne 0) { throw "备份复核失败：$(Protect-KanyikanText -Text $result.output)" }
    $jsonLine = @($result.output -split '\r?\n' | Where-Object { $_.Trim().StartsWith('{') }) | Select-Object -Last 1
    try { $metadata = ($jsonLine | ConvertFrom-Json).metadata }
    catch { throw '备份复核未返回合法公开元数据。' }
    if ([string]$metadata.productVersion -cne [string]$State.productVersion -or [string]$metadata.manifestSha256 -cne [string]$State.manifestSha256 -or [string]$metadata.releasePublicKeySha256 -cne [string]$State.releasePublicKeySha256) { throw '备份版本或发行信任元数据与当前安装不匹配。' }
    return [pscustomobject][ordered]@{ name = $name; path = $resolved; metadata = $metadata }
}

function Get-KanyikanRestoreArguments {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$BackupName
    )
    if ($BackupName -notmatch '^kanyikan-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$') { throw '备份名称不合法。' }
    return Get-KanyikanComposeArguments -InstallRoot $InstallRoot -Arguments @(
        'run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint', 'python', 'backend',
        '-m', 'app.tools.local_backup', 'restore', '--backup-root', '/backups',
        '--backup', "/backups/$BackupName", '--snapshots-root', '/app/data/snapshots',
        '--skills-root', '/app/data/workspace_skills'
    )
}

function Start-KanyikanPostgresForRestore {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [ValidateRange(1, 600)][int]$TimeoutSeconds = 120
    )
    $start = Invoke-KanyikanComposeCommand -InstallRoot $InstallRoot -Arguments @('up', '--detach', '--no-build', '--pull', 'never', 'postgres')
    if ($start.exitCode -ne 0) { throw "无法启动恢复所需 PostgreSQL：$(Protect-KanyikanText -Text $start.output)" }
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $facts = @(Get-KanyikanServiceFacts -InstallRoot $InstallRoot | Where-Object { $_.Service -ceq 'postgres' })
        if ($facts.Count -eq 1 -and $facts[0].State -ceq 'running' -and $facts[0].Health -ceq 'healthy') { return }
        if ([DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 2 }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw '等待 PostgreSQL 恢复就绪超时。'
}

function Invoke-KanyikanRestore {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$State,
        [Parameter(Mandatory = $true)][string]$BackupName
    )
    Start-KanyikanPostgresForRestore -InstallRoot $InstallRoot
    $result = Invoke-KanyikanDockerCommand -Arguments (Get-KanyikanRestoreArguments -InstallRoot $InstallRoot -BackupName $BackupName)
    if ($result.exitCode -ne 0) { throw "完整恢复失败：$(Protect-KanyikanText -Text $result.output)" }
    $jsonLine = @($result.output -split '\r?\n' | Where-Object { $_.Trim().StartsWith('{') }) | Select-Object -Last 1
    try { $metadata = $jsonLine | ConvertFrom-Json }
    catch { throw '完整恢复工具未返回合法公开元数据。' }
    if ([string]$metadata.status -cne 'restored' -or [string]$metadata.backup -cne $BackupName) { throw '完整恢复结果与请求不一致。' }
    return $metadata
}

function Get-KanyikanDoctorReport {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    $state = Read-KanyikanInstallState -InstallRoot $root
    $hostFacts = Get-KanyikanHostFacts -InstallRoot $root -ReadOnly
    $checks = @(
        [pscustomobject]@{ name = 'Windows 10/11 x64'; status = if ($hostFacts.isWindows -and $hostFacts.windowsMajorVersion -eq 10 -and $hostFacts.architecture -ceq 'AMD64') { '通过' } else { '失败' }; detail = $hostFacts.architecture },
        [pscustomobject]@{ name = 'Docker Desktop'; status = if ($hostFacts.dockerDesktopInstalled) { '通过' } else { '失败' }; detail = '仅检查安装状态' },
        [pscustomobject]@{ name = 'Docker Engine'; status = if ($hostFacts.dockerEngineAvailable) { '通过' } else { '失败' }; detail = [string]$hostFacts.dockerOsType },
        [pscustomobject]@{ name = 'Docker Compose v2'; status = if ($hostFacts.composeMajorVersion -eq 2) { '通过' } else { '失败' }; detail = [string]$hostFacts.composeMajorVersion },
        [pscustomobject]@{ name = 'Docker Proxy'; status = if ($hostFacts.dockerProxyEnabled) { '已启用' } else { '未启用' }; detail = '代理地址与凭据不采集' },
        [pscustomobject]@{ name = 'Execution Provider'; status = '未配置或未检查'; detail = '请登录后在设置页查看；不影响 Bootstrap Ready' }
    )
    if ($state.currentState -cne 'NEW') {
        try {
            $release = Test-KanyikanReleasePackage -PackageRoot $root -TrustedPublicKeySha256 ([string]$state.releasePublicKeySha256)
            $checks += [pscustomobject]@{ name = '发行包'; status = '通过'; detail = [string]$release.version }
        }
        catch { $checks += [pscustomobject]@{ name = '发行包'; status = '失败'; detail = Protect-KanyikanText -Text $_.Exception.Message; }; $release = $null }
        if ($null -ne $release) {
            try { [void](Test-KanyikanReleaseImagesPresent -Manifest $release.manifest); $checks += [pscustomobject]@{ name = '六个镜像'; status = '通过'; detail = '身份、RepoDigest、linux/amd64' } }
            catch { $checks += [pscustomobject]@{ name = '六个镜像'; status = '失败'; detail = Protect-KanyikanText -Text $_.Exception.Message } }
            try {
                if (-not (Test-KanyikanSystemEnvironment -Path ([System.IO.Path]::Combine($root, 'config', 'system.env')) -Manifest $release.manifest)) { throw 'system.env 内容或 ACL 不合格。' }
                $checks += [pscustomobject]@{ name = '系统配置'; status = '通过'; detail = '必需键与 ACL 合格' }
            }
            catch { $checks += [pscustomobject]@{ name = '系统配置'; status = '失败'; detail = Protect-KanyikanText -Text $_.Exception.Message } }
            try { [void](Test-KanyikanLocalCertificate -Manifest $release.manifest -CertificateDirectory ([System.IO.Path]::Combine($root, 'config', 'certs'))); $checks += [pscustomobject]@{ name = '本地 TLS'; status = '通过'; detail = 'SAN、用途、有效期、私钥与 ACL' } }
            catch { $checks += [pscustomobject]@{ name = '本地 TLS'; status = '失败'; detail = Protect-KanyikanText -Text $_.Exception.Message } }
        }
        try {
            $serviceResult = Test-KanyikanServiceFacts -Facts @(Get-KanyikanServiceFacts -InstallRoot $root)
            $checks += [pscustomobject]@{ name = '十个服务与唯一端口'; status = if ($serviceResult.passed) { '通过' } else { '失败' }; detail = if ($serviceResult.passed) { '全部健康' } else { $serviceResult.reason } }
        }
        catch { $checks += [pscustomobject]@{ name = '十个服务与唯一端口'; status = '失败'; detail = Protect-KanyikanText -Text $_.Exception.Message } }
        if ([System.IO.File]::Exists([System.IO.Path]::Combine($root, 'config', 'certs', 'localhost.crt'))) {
            $endpoint = Test-KanyikanBootstrapEndpoints -LeafCertificatePath ([System.IO.Path]::Combine($root, 'config', 'certs', 'localhost.crt'))
            $checks += [pscustomobject]@{ name = 'Bootstrap Endpoints'; status = if ($endpoint.passed) { '通过' } else { '失败' }; detail = if ($endpoint.passed) { '/health 与 /ready' } else { $endpoint.reason } }
        }
    }
    return [pscustomobject][ordered]@{
        generatedAt = [DateTime]::UtcNow.ToString('o')
        productVersion = $state.productVersion
        installState = $state.currentState
        entrypoint = 'https://127.0.0.1:10443'
        capacities = [pscustomobject]@{ cpuCores = $hostFacts.cpuCores; memoryBytes = $hostFacts.memoryBytes; freeDiskBytes = $hostFacts.freeDiskBytes }
        checks = $checks
    }
}

function Test-KanyikanSupportPayload {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    $forbiddenPatterns = @(
        '(?is)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----',
        '(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}',
        '(?i)\b(?:eyJ[A-Za-z0-9_-]{5,})\.(?:[A-Za-z0-9_-]{5,})\.(?:[A-Za-z0-9_-]{5,})\b',
        '(?i)\b(?:SECRET_KEY|CONFIG_ENCRYPTION_KEY|ADMIN_PASSWORD|POSTGRES_PASSWORD|REDIS_PASSWORD|BROWSERLESS_TOKEN|API_KEY|COOKIE|JWT|SENTRY_DSN)\s*[:=]\s*["'']?(?!\[REDACTED\])[^\s,"''}]+',
        '(?i)https?://[^\s:/@]+:[^\s/@]+@'
    )
    foreach ($pattern in $forbiddenPatterns) { if ([regex]::IsMatch($Text, $pattern)) { return $false } }
    return $true
}

function Export-KanyikanSupportBundle {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    $state = Read-KanyikanInstallState -InstallRoot $root
    $doctor = Get-KanyikanDoctorReport -InstallRoot $root
    $releasePublic = $null
    if ($state.currentState -cne 'NEW') {
        try {
            $release = Test-KanyikanReleasePackage -PackageRoot $root -TrustedPublicKeySha256 ([string]$state.releasePublicKeySha256)
            $releasePublic = [pscustomobject][ordered]@{
                release = $release.manifest.release
                target = $release.manifest.target
                entrypoint = $release.manifest.entrypoint
                images = @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless') | ForEach-Object { [pscustomobject]@{ name = $_; reference = [string]$release.manifest.images.$_.reference; imageId = [string]$release.manifest.images.$_.imageId; platform = [string]$release.manifest.images.$_.platform } }
                signing = [pscustomobject]@{ algorithm = [string]$release.manifest.signing.algorithm; keyId = [string]$release.manifest.signing.keyId; publicKeySha256 = [string]$release.manifest.signing.publicKeySha256 }
            }
        }
        catch { $releasePublic = [pscustomobject]@{ verification = 'failed'; reason = Protect-KanyikanText -Text $_.Exception.Message } }
    }
    $containers = @()
    try {
        foreach ($fact in @(Get-KanyikanServiceFacts -InstallRoot $root)) { $containers += [pscustomobject][ordered]@{ service = [string]$fact.Service; state = [string]$fact.State; health = [string]$fact.Health; image = [string]$fact.Image; publishers = @($fact.Publishers) } }
    }
    catch { $containers = @([pscustomobject]@{ status = 'unavailable' }) }
    $publicState = [pscustomobject][ordered]@{
        contractVersion = $state.contractVersion; productVersion = $state.productVersion; currentState = $state.currentState
        updatedAt = $state.updatedAt; manifestSha256 = $state.manifestSha256; releasePublicKeySha256 = $state.releasePublicKeySha256
        composeProjectName = $state.composeProjectName; resources = $state.resources; images = $state.images
        caThumbprint = $state.caThumbprint; caTrusted = $state.caTrusted
        lastFailure = if ($null -eq $state.lastFailure) { $null } else { [pscustomobject]@{ occurredAt = $state.lastFailure.occurredAt; command = $state.lastFailure.command; stage = $state.lastFailure.stage; exitCode = $state.lastFailure.exitCode; reason = Protect-KanyikanText -Text ([string]$state.lastFailure.reason) } }
    }
    $installerLogs = @()
    $logDirectory = [System.IO.Path]::Combine($root, 'logs')
    if ([System.IO.Directory]::Exists($logDirectory)) {
        foreach ($logPath in @([System.IO.Directory]::GetFiles($logDirectory, 'kanyikan-*.log') | Sort-Object -Descending | Select-Object -First 5)) {
            $content = Protect-KanyikanText -Text ([System.IO.File]::ReadAllText($logPath, [Text.Encoding]::UTF8))
            if (-not (Test-KanyikanSupportPayload -Text $content)) { throw "安装器日志敏感信息扫描失败：$([System.IO.Path]::GetFileName($logPath))" }
            $installerLogs += [pscustomobject]@{ fileName = [System.IO.Path]::GetFileName($logPath); content = $content }
        }
    }
    $payload = [pscustomobject][ordered]@{
        schemaVersion = 1; generatedAt = [DateTime]::UtcNow.ToString('o'); doctor = $doctor; installation = $publicState
        release = $releasePublic; containers = $containers; installerLogs = $installerLogs
        exclusions = @('system.env', 'private keys', 'Provider request/response bodies', 'customer business content', 'raw container configuration')
    }
    $json = Protect-KanyikanText -Text ($payload | ConvertTo-Json -Depth 16)
    if (-not (Test-KanyikanSupportPayload -Text $json)) { throw '支持包敏感信息扫描失败，已拒绝交付。' }

    $supportRoot = [System.IO.Path]::Combine($root, 'data', 'support-bundles')
    [System.IO.Directory]::CreateDirectory($supportRoot) | Out-Null
    Set-KanyikanRestrictedDirectoryAcl -Path $supportRoot
    $identifier = "$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
    $staging = [System.IO.Path]::Combine($supportRoot, ".support-$identifier")
    $temporaryZip = [System.IO.Path]::Combine($supportRoot, ".support-bundle-$identifier.tmp")
    $destination = [System.IO.Path]::Combine($supportRoot, "support-bundle-$identifier.zip")
    [System.IO.Directory]::CreateDirectory($staging) | Out-Null
    try {
        $payloadPath = [System.IO.Path]::Combine($staging, 'support-bundle.json')
        [System.IO.File]::WriteAllText($payloadPath, $json, (New-Object Text.UTF8Encoding($false)))
        Set-KanyikanRestrictedFileAcl -Path $payloadPath
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory($staging, $temporaryZip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
        $archive = [System.IO.Compression.ZipFile]::OpenRead($temporaryZip)
        try {
            if ($archive.Entries.Count -ne 1 -or $archive.Entries[0].FullName -cne 'support-bundle.json') { throw '支持包 ZIP 文件集合不合法。' }
            $reader = New-Object System.IO.StreamReader($archive.Entries[0].Open(), [Text.Encoding]::UTF8)
            try { $archivedText = $reader.ReadToEnd() } finally { $reader.Dispose() }
            if (-not (Test-KanyikanSupportPayload -Text $archivedText)) { throw '支持包 ZIP 二次敏感信息扫描失败。' }
        }
        finally { $archive.Dispose() }
        Set-KanyikanRestrictedFileAcl -Path $temporaryZip
        if (-not [KanyikanNativeMethods]::MoveFileEx($temporaryZip, $destination, (0x1 -bor 0x8))) { throw (New-Object ComponentModel.Win32Exception([Runtime.InteropServices.Marshal]::GetLastWin32Error())) }
        return [pscustomobject][ordered]@{ path = $destination; fileName = [System.IO.Path]::GetFileName($destination) }
    }
    finally {
        if ([System.IO.Directory]::Exists($staging)) { [System.IO.Directory]::Delete($staging, $true) }
        if ([System.IO.File]::Exists($temporaryZip)) { [System.IO.File]::Delete($temporaryZip) }
    }
}

function Set-KanyikanInstallationActive {
    param(
        [Parameter(Mandatory = $true)][psobject]$State,
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][bool]$Active
    )
    Assert-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    $State.installationActive = $Active
    $State.updatedAt = [DateTime]::UtcNow.ToString('o')
    Write-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    return $State
}

function Get-KanyikanUninstallResourcePlan {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$State,
        [Parameter(Mandatory = $true)][psobject]$Manifest
    )
    Assert-KanyikanInstallState -State $State -InstallRoot $InstallRoot
    foreach ($name in @('postgres', 'redis', 'snapshots', 'skills')) {
        if ([string]$State.resources.volumes.$name -cne [string]$Manifest.resources.namedVolumes.$name) { throw "无法共同证明数据卷归属：$name" }
    }
    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    return [pscustomobject][ordered]@{
        composeDownArguments = @(Get-KanyikanComposeArguments -InstallRoot $root -Arguments @('down', '--remove-orphans'))
        volumes = @(
            [string]$State.resources.volumes.postgres,
            [string]$State.resources.volumes.redis,
            [string]$State.resources.volumes.snapshots,
            [string]$State.resources.volumes.skills
        )
        generatedPaths = @(
            [System.IO.Path]::Combine($root, 'config', 'system.env'),
            [System.IO.Path]::Combine($root, 'config', 'certs'),
            [System.IO.Path]::Combine($root, 'state'),
            [System.IO.Path]::Combine($root, 'data'),
            [System.IO.Path]::Combine($root, 'logs')
        )
        caThumbprint = if ($State.caTrusted) { [string]$State.caThumbprint } else { $null }
    }
}

function Assert-KanyikanSafeRemovalTree {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $root = Get-KanyikanNormalizedPath -Path $InstallRoot
    $target = Get-KanyikanNormalizedPath -Path $Path
    if (-not $target.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw '拒绝删除安装根目录之外的路径。' }
    if ([System.IO.File]::Exists($target)) {
        if (([System.IO.File]::GetAttributes($target) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "删除目标包含重解析点，已保留并需人工处理：$target" }
        return
    }
    if ([System.IO.Directory]::Exists($target)) {
        $pending = New-Object 'System.Collections.Generic.Stack[string]'
        $pending.Push($target)
        while ($pending.Count -gt 0) {
            $current = $pending.Pop()
            if (([System.IO.File]::GetAttributes($current) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "删除目标包含重解析点，已保留并需人工处理：$current" }
            foreach ($entry in [System.IO.Directory]::GetFileSystemEntries($current)) {
                $attributes = [System.IO.File]::GetAttributes($entry)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "删除目标包含重解析点，已保留并需人工处理：$entry" }
                if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { $pending.Push($entry) }
            }
        }
    }
}

function Invoke-KanyikanUninstall {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$State,
        [Parameter(Mandatory = $true)][psobject]$Manifest,
        [switch]$PurgeData
    )
    $plan = Get-KanyikanUninstallResourcePlan -InstallRoot $InstallRoot -State $State -Manifest $Manifest
    if ($PurgeData) {
        foreach ($path in $plan.generatedPaths) { Assert-KanyikanSafeRemovalTree -InstallRoot $InstallRoot -Path $path }
    }
    $down = Invoke-KanyikanDockerCommand -Arguments $plan.composeDownArguments
    if ($down.exitCode -ne 0) { throw "删除项目容器和网络失败：$(Protect-KanyikanText -Text $down.output)" }
    if (-not [string]::IsNullOrWhiteSpace([string]$plan.caThumbprint) -and $plan.caThumbprint -match '^[0-9A-Fa-f]{40}$') { [void](Remove-KanyikanLocalRootTrust -Thumbprint $plan.caThumbprint) }
    if (-not $PurgeData) {
        $State.caTrusted = $false
        return Set-KanyikanInstallationActive -State $State -InstallRoot $InstallRoot -Active $false
    }
    foreach ($volume in $plan.volumes) {
        $inspect = Invoke-KanyikanDockerCommand -Arguments @('volume', 'inspect', $volume)
        if ($inspect.exitCode -eq 0) {
            $remove = Invoke-KanyikanDockerCommand -Arguments @('volume', 'rm', $volume)
            if ($remove.exitCode -ne 0) { throw "删除数据卷失败：$volume" }
        }
    }
    foreach ($path in $plan.generatedPaths) {
        if ([System.IO.Directory]::Exists($path)) { [System.IO.Directory]::Delete($path, $true) }
        elseif ([System.IO.File]::Exists($path)) { [System.IO.File]::Delete($path) }
    }
    return $null
}

function Get-KanyikanLatestValidBackup {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][psobject]$State
    )
    $backupRoot = [System.IO.Path]::Combine((Get-KanyikanNormalizedPath -Path $InstallRoot), 'data', 'backups')
    if (-not [System.IO.Directory]::Exists($backupRoot)) { return $null }
    foreach ($directory in @([System.IO.Directory]::GetDirectories($backupRoot, 'kanyikan-*') | Sort-Object -Descending)) {
        try { return Invoke-KanyikanValidateBackup -InstallRoot $InstallRoot -State $State -BackupPath $directory }
        catch { }
    }
    return $null
}

Export-ModuleMember -Function @(
    'Assert-KanyikanSafeRemovalTree',
    'Compare-KanyikanSemanticVersion',
    'Expand-KanyikanUpdatePackage',
    'Export-KanyikanSupportBundle',
    'Get-KanyikanCertificateDockerArguments',
    'Get-KanyikanBackupArguments',
    'Get-KanyikanComposeArguments',
    'Get-KanyikanInstallStates',
    'Get-KanyikanLocalCaThumbprint',
    'Get-KanyikanStatePath',
    'Get-KanyikanHostFacts',
    'Get-KanyikanDoctorReport',
    'Get-KanyikanRestoreArguments',
    'Get-KanyikanLatestValidBackup',
    'Get-KanyikanInspectedImages',
    'Get-KanyikanServiceFacts',
    'Get-KanyikanUninstallResourcePlan',
    'Import-KanyikanReleaseImages',
    'Install-KanyikanLocalRootTrust',
    'Invoke-KanyikanBackup',
    'Invoke-KanyikanRestore',
    'Invoke-KanyikanUninstall',
    'Invoke-KanyikanValidateBackup',
    'Invoke-KanyikanPreflight',
    'New-KanyikanInstallState',
    'New-KanyikanReleaseAssetSnapshot',
    'New-KanyikanLogFile',
    'New-KanyikanLocalCertificate',
    'Read-KanyikanAdminPassword',
    'Read-KanyikanInstallState',
    'Remove-KanyikanLocalRootTrust',
    'Remove-KanyikanReleaseAssetSnapshot',
    'Resolve-KanyikanBackupDirectory',
    'Restore-KanyikanReleaseAssets',
    'Set-KanyikanInstallState',
    'Set-KanyikanReleaseAssets',
    'Set-KanyikanInstallFailure',
    'Set-KanyikanInstallationActive',
    'Set-KanyikanBackupAcl',
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
    'Test-KanyikanSupportPayload',
    'Test-KanyikanUpgradePath',
    'Test-KanyikanServiceFacts',
    'Wait-KanyikanBootstrapReady',
    'Write-KanyikanLog',
    'Write-KanyikanSystemEnvironment'
)
