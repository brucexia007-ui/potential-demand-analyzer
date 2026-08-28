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

Export-ModuleMember -Function @(
    'Get-KanyikanInstallStates',
    'Get-KanyikanStatePath',
    'Get-KanyikanHostFacts',
    'Invoke-KanyikanPreflight',
    'New-KanyikanInstallState',
    'Read-KanyikanInstallState',
    'Set-KanyikanInstallState',
    'Set-KanyikanInstallFailure',
    'Test-KanyikanPreflightFacts'
)
