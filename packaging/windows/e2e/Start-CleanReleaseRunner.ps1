[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet(1, 2, 3)]
    [int]$Round,

    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$SnapshotSha256,

    [Parameter(Mandatory)]
    [ValidatePattern('^https://github\.com/[^/]+/[^/]+$')]
    [string]$RepositoryUrl,

    [Parameter(Mandatory)]
    [string]$EnterOfflineScript,

    [Parameter(Mandatory)]
    [string]$ExitOfflineScript,

    [Parameter(Mandatory)]
    [string]$InfrastructureHooksRoot,

    [string]$RunnerRoot = 'C:\actions-runner',

    [switch]$PreflightOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-LeafFile {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$Description
    )

    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "$Description must be an absolute path."
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description does not exist: $Path"
    }
}

function Assert-LastExitCode {
    param(
        [Parameter(Mandatory)]
        [int]$ExitCode,

        [Parameter(Mandatory)]
        [string]$Operation
    )

    if ($ExitCode -ne 0) {
        throw "$Operation failed with exit code $ExitCode."
    }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this launcher from an elevated PowerShell session.'
}

$windows = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
if ($windows.InstallationType -cne 'Client' -or [int]$windows.CurrentBuild -lt 22000) {
    throw 'The clean release runner must use Windows 11.'
}

$computer = Get-CimInstance -ClassName Win32_ComputerSystem
if ([int]$computer.NumberOfLogicalProcessors -lt 4) {
    throw 'The clean release runner requires at least 4 logical processors.'
}
if ([uint64]$computer.TotalPhysicalMemory -lt 8GB) {
    throw 'The clean release runner requires at least 8 GiB of memory.'
}

$systemDrive = [Environment]::GetEnvironmentVariable('SystemDrive', 'Machine')
$drive = [IO.DriveInfo]::new($systemDrive)
if ([uint64]$drive.AvailableFreeSpace -lt 20GB) {
    throw 'The clean release runner requires at least 20 GiB free on the system drive.'
}

if ([Environment]::GetEnvironmentVariable('KANYIKAN_CLEAN_E2E', 'Machine') -cne '1') {
    throw 'The machine-level KANYIKAN_CLEAN_E2E marker must equal 1.'
}

$markerPath = Join-Path $env:ProgramData 'KanyikanCleanE2E\snapshot-consumed.json'
if (Test-Path -LiteralPath $markerPath) {
    throw 'snapshot-consumed.json exists; restore the immutable golden snapshot.'
}

if (-not [IO.Path]::IsPathRooted($RunnerRoot)) {
    throw 'RunnerRoot must be an absolute path.'
}
$resolvedRunnerRoot = (Resolve-Path -LiteralPath $RunnerRoot).Path
$configPath = Join-Path $resolvedRunnerRoot 'config.cmd'
$runPath = Join-Path $resolvedRunnerRoot 'run.cmd'
Assert-LeafFile -Path $configPath -Description 'config.cmd'
Assert-LeafFile -Path $runPath -Description 'run.cmd'

foreach ($configurationFile in @('.runner', '.credentials', '.credentials_rsaparams', '.service')) {
    if (Test-Path -LiteralPath (Join-Path $resolvedRunnerRoot $configurationFile)) {
        throw "Runner is already configured ($configurationFile); restore the golden snapshot."
    }
}

Assert-LeafFile -Path $EnterOfflineScript -Description 'Enter-offline script'
Assert-LeafFile -Path $ExitOfflineScript -Description 'Exit-offline script'
if (-not [IO.Path]::IsPathRooted($InfrastructureHooksRoot)) {
    throw 'InfrastructureHooksRoot must be an absolute path.'
}
$resolvedHooksRoot = (Resolve-Path -LiteralPath $InfrastructureHooksRoot).Path
foreach ($hookName in @(
    'Enter-DockerStopped.ps1',
    'Exit-DockerStopped.ps1',
    'Enter-WindowsContainers.ps1',
    'Exit-WindowsContainers.ps1',
    'Enter-DiskInsufficient.ps1',
    'Exit-DiskInsufficient.ps1'
)) {
    Assert-LeafFile -Path (Join-Path $resolvedHooksRoot $hookName) -Description $hookName
}

$dockerOs = (& docker info --format '{{.OSType}}' 2>&1 | Out-String).Trim()
Assert-LastExitCode -ExitCode $LASTEXITCODE -Operation 'docker info'
if ($dockerOs -cne 'linux') {
    throw "Docker must use Linux containers; observed: $dockerOs"
}
$composeVersion = (& docker compose version --short 2>&1 | Out-String).Trim()
Assert-LastExitCode -ExitCode $LASTEXITCODE -Operation 'docker compose version'
if ($composeVersion -cnotmatch '^v?2\.') {
    throw "Docker Compose v2 is required; observed: $composeVersion"
}

$preflight = [ordered]@{
    status = 'READY'
    round = $Round
    snapshotSha256 = $SnapshotSha256
    windowsBuild = [int]$windows.CurrentBuild
    dockerOs = $dockerOs
    composeVersion = $composeVersion
    runnerRoot = $resolvedRunnerRoot
}
if ($PreflightOnly) {
    $preflight | ConvertTo-Json -Compress
    return
}

$registrationToken = [Environment]::GetEnvironmentVariable(
    'KANYIKAN_RUNNER_REGISTRATION_TOKEN',
    'Process'
)
if ([string]::IsNullOrWhiteSpace($registrationToken)) {
    throw 'KANYIKAN_RUNNER_REGISTRATION_TOKEN is missing from the current process.'
}

$generationId = [Guid]::NewGuid().ToString('D')
$runnerName = "kanyikan-clean-round-$Round-$($generationId.Substring(0, 8))"
$labels = "kanyikan-clean-e2e,kanyikan-clean-e2e-round-$Round"

$env:KANYIKAN_CLEAN_E2E_GENERATION_ID = $generationId
$env:KANYIKAN_CLEAN_SNAPSHOT_SHA256 = $SnapshotSha256
$env:KANYIKAN_CLEAN_E2E_ROUND = [string]$Round
$env:KANYIKAN_ENTER_OFFLINE_SCRIPT = (Resolve-Path -LiteralPath $EnterOfflineScript).Path
$env:KANYIKAN_EXIT_OFFLINE_SCRIPT = (Resolve-Path -LiteralPath $ExitOfflineScript).Path
$env:KANYIKAN_INFRASTRUCTURE_HOOKS_ROOT = $resolvedHooksRoot

Push-Location -LiteralPath $resolvedRunnerRoot
try {
    try {
        & $configPath `
            --unattended `
            --ephemeral `
            --url $RepositoryUrl `
            --token $registrationToken `
            --name $runnerName `
            --labels $labels `
            --work '_work'
        Assert-LastExitCode -ExitCode $LASTEXITCODE -Operation 'config.cmd'
    }
    finally {
        [Environment]::SetEnvironmentVariable(
            'KANYIKAN_RUNNER_REGISTRATION_TOKEN',
            $null,
            'Process'
        )
        $registrationToken = $null
    }

    & $runPath
    $runnerExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($runnerExitCode -ne 0) {
    throw "run.cmd failed with exit code $runnerExitCode."
}

[ordered]@{
    status = 'ONE_EPHEMERAL_JOB_COMPLETED'
    round = $Round
    generationId = $generationId
    snapshotSha256 = $SnapshotSha256
    runnerName = $runnerName
} | ConvertTo-Json -Compress
