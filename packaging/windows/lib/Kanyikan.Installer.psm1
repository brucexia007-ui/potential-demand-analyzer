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

Export-ModuleMember -Function @(
    'Get-KanyikanInstallStates',
    'Get-KanyikanStatePath',
    'New-KanyikanInstallState',
    'Read-KanyikanInstallState',
    'Set-KanyikanInstallState',
    'Set-KanyikanInstallFailure'
)
