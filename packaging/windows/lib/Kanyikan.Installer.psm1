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
        [psobject]$Facts
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
        [pscustomobject]@{ name = '127.0.0.1:10443 available'; passed = [bool]$Facts.portAvailable; exitCode = 22; remediation = '请释放本机 TCP 端口 10443；安装器不会结束占用进程。' },
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
        [string]$InstallRoot
    )

    $facts = Get-KanyikanHostFacts -InstallRoot $InstallRoot
    return Test-KanyikanPreflightFacts -Facts $facts
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

Export-ModuleMember -Function @(
    'Get-KanyikanInstallStates',
    'Get-KanyikanStatePath',
    'Get-KanyikanHostFacts',
    'Invoke-KanyikanPreflight',
    'New-KanyikanInstallState',
    'Read-KanyikanInstallState',
    'Set-KanyikanInstallState',
    'Set-KanyikanInstallFailure',
    'Test-KanyikanPreflightFacts',
    'Test-KanyikanReleasePackage'
)
