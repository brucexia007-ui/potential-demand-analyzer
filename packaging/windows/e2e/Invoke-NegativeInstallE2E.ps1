[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OfflineZip,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedPublicKeySha256,
    [Parameter(Mandatory = $true)][string]$InfrastructureHooksRoot,
    [Parameter(Mandatory = $true)][string]$EvidencePath,
    [ValidateSet('PortOccupied', 'DockerStopped', 'WindowsContainers', 'DiskInsufficient', 'ManifestTampered', 'ImageArchiveCorrupt', 'InterruptionRetry')]
    [string[]]$Scenario = @('PortOccupied', 'DockerStopped', 'WindowsContainers', 'DiskInsufficient', 'ManifestTampered', 'ImageArchiveCorrupt', 'InterruptionRetry')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

Import-Module (Join-Path $PSScriptRoot 'Kanyikan.ReleaseE2E.psm1') -Force
Assert-KanyikanDedicatedE2EHost

$offlineZipPath = (Resolve-Path -LiteralPath $OfflineZip).Path
$hooksRoot = [IO.Path]::GetFullPath($InfrastructureHooksRoot)
$evidenceFullPath = [IO.Path]::GetFullPath($EvidencePath)
$wrapperPath = Join-Path $PSScriptRoot 'Invoke-ControllerWithAnswers.ps1'
$normalScenarioRoot = [IO.Path]::Combine($env:RUNNER_TEMP, '看一看 E2E 负向测试')
$expectedExitCodes = @{
    PortOccupied = 22
    DockerStopped = 21
    WindowsContainers = 21
    DiskInsufficient = 22
    ManifestTampered = 30
    ImageArchiveCorrupt = 30
    InterruptionRetry = 0
}
$failureSignals = @{
    PortOccupied = '127.0.0.1:10443 available'
    DockerStopped = 'Docker Engine'
    WindowsContainers = 'Linux Containers'
    DiskInsufficient = 'Disk >= 20 GiB'
    ManifestTampered = '阶段=VERIFIED'
    ImageArchiveCorrupt = '阶段=VERIFIED'
}
$results = New-Object 'System.Collections.Generic.List[object]'
$startedAt = [DateTime]::UtcNow
$terminalFailure = $null

function Invoke-DockerForE2E {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = & docker.exe @Arguments 2>&1
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    return [pscustomobject]@{ exitCode = $code; output = (@($lines) -join [Environment]::NewLine).Trim() }
}

function Get-ToolVersionForEvidence {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $result = Invoke-DockerForE2E -Arguments $Arguments
    if ($result.exitCode -ne 0) { throw "无法读取 Docker 验收环境版本：$($Arguments -join ' ')" }
    return $result.output
}

function Assert-PathWithinParent {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent
    )
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $fullParent = [IO.Path]::GetFullPath($Parent).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if ($fullPath.Length -le $fullParent.Length -or -not $fullPath.StartsWith($fullParent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "E2E 临时路径越界：$fullPath"
    }
}

function Remove-ScenarioTree {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedParent
    )
    if (-not [IO.Directory]::Exists($Path)) { return }
    Assert-PathWithinParent -Path $Path -Parent $AllowedParent
    if (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "拒绝删除 E2E 重解析点：$Path" }
    [IO.Directory]::Delete($Path, $true)
}

function Assert-ZipEntrySafe {
    param(
        [Parameter(Mandatory = $true)][string]$EntryName,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )
    if ([string]::IsNullOrWhiteSpace($EntryName) -or $EntryName.Contains('\') -or $EntryName.StartsWith('/') -or $EntryName -match '^[A-Za-z]:' -or $EntryName.Contains('//')) {
        throw "离线 ZIP 含非法路径：$EntryName"
    }
    $trimmed = $EntryName.TrimEnd('/')
    foreach ($segment in $trimmed.Split('/')) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -ceq '.' -or $segment -ceq '..') { throw "离线 ZIP 含非法路径段：$EntryName" }
    }
    $relativeWindowsPath = $EntryName.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $target = [IO.Path]::GetFullPath([IO.Path]::Combine($DestinationRoot, $relativeWindowsPath))
    Assert-PathWithinParent -Path $target -Parent $DestinationRoot
}

function Expand-ScenarioPackage {
    param(
        [Parameter(Mandatory = $true)][string]$ScenarioName,
        [Parameter(Mandatory = $true)][string]$ParentRoot
    )
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Directory]::CreateDirectory($ParentRoot) | Out-Null
    $installParent = [IO.Path]::Combine($ParentRoot, "$ScenarioName-$([Guid]::NewGuid().ToString('N'))")
    [IO.Directory]::CreateDirectory($installParent) | Out-Null
    $result = $null
    try {
        $archive = [IO.Compression.ZipFile]::OpenRead($offlineZipPath)
        try {
            $roots = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
            foreach ($entry in $archive.Entries) {
                Assert-ZipEntrySafe -EntryName $entry.FullName -DestinationRoot $installParent
                [void]$roots.Add($entry.FullName.Split('/')[0])
            }
            if ($roots.Count -ne 1) { throw '离线 ZIP 必须恰好包含一个顶层目录。' }
            $packageDirectoryName = @($roots)[0]
            if ($packageDirectoryName -notmatch '^Kanyikan-v.+-windows-amd64$') { throw '离线 ZIP 顶层发行目录不合法。' }
        }
        finally { $archive.Dispose() }
        [IO.Compression.ZipFile]::ExtractToDirectory($offlineZipPath, $installParent)
        $packageRoot = [IO.Path]::Combine($installParent, $packageDirectoryName)
        $controllerPath = [IO.Path]::Combine($packageRoot, 'kanyikan.ps1')
        $controllerSource = [IO.File]::ReadAllText($controllerPath, [Text.Encoding]::UTF8)
        if (-not $controllerSource.Contains($ExpectedPublicKeySha256)) { throw '离线包控制器的发行公钥指纹与 E2E 输入不一致。' }
        $manifest = [IO.File]::ReadAllText(([IO.Path]::Combine($packageRoot, 'release-manifest.json')), [Text.Encoding]::UTF8) | ConvertFrom-Json
        $result = [pscustomobject]@{
            installParent = $installParent
            packageRoot = $packageRoot
            controllerPath = $controllerPath
            releaseVersion = [string]$manifest.release.version
        }
        return $result
    }
    finally {
        if ($null -eq $result -and [IO.Directory]::Exists($installParent)) {
            Remove-ScenarioTree -Path $installParent -AllowedParent $ParentRoot
        }
    }
}

function Get-ReleaseVersionFromOfflineZip {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($offlineZipPath)
    try {
        $entries = @($archive.Entries | Where-Object { $_.FullName -match '^[^/]+/release-manifest\.json$' })
        if ($entries.Count -ne 1) { throw '离线 ZIP 缺少唯一 release-manifest.json。' }
        $reader = New-Object IO.StreamReader($entries[0].Open(), (New-Object Text.UTF8Encoding($false)), $true)
        try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json }
        finally { $reader.Dispose() }
        if ([string]$manifest.release.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw '离线 ZIP 的发行版本不合法。' }
        return [string]$manifest.release.version
    }
    finally { $archive.Dispose() }
}

function Invoke-ScenarioHook {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Enter', 'Exit')][string]$Action,
        [Parameter(Mandatory = $true)][string]$ScenarioName
    )
    $path = [IO.Path]::Combine($hooksRoot, "$Action-$ScenarioName.ps1")
    if (-not [IO.File]::Exists($path)) { throw "缺少 E2E 基础设施 Hook：$path" }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $path 2>&1
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne 0) { throw "E2E 基础设施 Hook 失败：$Action-$ScenarioName；退出码=$code" }
    $jsonLine = @($lines | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) | Select-Object -Last 1
    if ([string]::IsNullOrWhiteSpace($jsonLine) -or -not $jsonLine.Trim().StartsWith('{')) { return $null }
    try { return $jsonLine | ConvertFrom-Json }
    catch { throw "E2E 基础设施 Hook 的末行不是合法 JSON：$Action-$ScenarioName" }
}

function Assert-KanyikanRuntimeAbsent {
    $engine = Invoke-DockerForE2E -Arguments @('info', '--format', '{{.OSType}}')
    if ($engine.exitCode -ne 0) { throw '无法在基础设施恢复后复核 Docker 资源边界。' }
    foreach ($arguments in @(
        @('ps', '-a', '--filter', 'label=com.docker.compose.project=kanyikan', '--quiet'),
        @('network', 'ls', '--filter', 'label=com.docker.compose.project=kanyikan', '--quiet'),
        @('volume', 'ls', '--filter', 'label=com.docker.compose.project=kanyikan', '--quiet')
    )) {
        $result = Invoke-DockerForE2E -Arguments $arguments
        if ($result.exitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($result.output)) { throw "检测到残留的 Kanyikan Compose 资源：$($arguments[0])" }
    }
}

function Assert-EarlyFailureHasNoPersistentSideEffects {
    param([Parameter(Mandatory = $true)][string]$PackageRoot)
    foreach ($relativePath in @(
        'state\install-state.json',
        'config\system.env',
        'config\certs\certificate-metadata.json',
        'data\backups'
    )) {
        $path = [IO.Path]::Combine($PackageRoot, $relativePath)
        if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) { throw "失败场景留下持久副作用：$relativePath" }
    }
    $logsPath = [IO.Path]::Combine($PackageRoot, 'logs')
    if ([IO.Directory]::Exists($logsPath) -and @(Get-ChildItem -LiteralPath $logsPath -File -Filter '*.jsonl').Count -gt 0) { throw '失败场景留下安装日志，说明已越过持久副作用边界。' }
}

function Invoke-ControllerForEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$ControllerPath,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$CredentialSecret,
        [switch]$PurgeData
    )
    $handle = Start-KanyikanE2EController -WrapperPath $wrapperPath -ControllerPath $ControllerPath -Command $Command -CredentialSecret $CredentialSecret -PurgeData:$PurgeData
    $handle.process.WaitForExit()
    $actualExitCode = [int]$handle.process.ExitCode
    return Complete-KanyikanE2EController -Handle $handle -AllowedExitCodes @($actualExitCode)
}

function New-RunEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$ScenarioName,
        [Parameter(Mandatory = $true)][int]$ExpectedExitCode,
        [Parameter(Mandatory = $true)][psobject]$Run,
        [Parameter(Mandatory = $true)][DateTime]$ScenarioStartedAt
    )
    return [pscustomobject][ordered]@{
        scenario = $ScenarioName
        command = 'install'
        expectedExitCode = $ExpectedExitCode
        actualExitCode = [int]$Run.exitCode
        outputSha256 = [string]$Run.outputSha256
        startedAt = $ScenarioStartedAt.ToString('o')
        completedAt = [DateTime]::UtcNow.ToString('o')
        passed = [int]$Run.exitCode -eq $ExpectedExitCode
    }
}

function Assert-ExpectedFailure {
    param(
        [Parameter(Mandatory = $true)][string]$ScenarioName,
        [Parameter(Mandatory = $true)][psobject]$Run
    )
    $expectedExitCode = [int]$expectedExitCodes[$ScenarioName]
    if ([int]$Run.exitCode -ne $expectedExitCode) { throw "$ScenarioName 退出码错误：期望=$expectedExitCode，实际=$($Run.exitCode)" }
    $combined = [string]$Run.stdout + "`n" + [string]$Run.stderr
    if (-not $combined.Contains([string]$failureSignals[$ScenarioName])) { throw "$ScenarioName 未命中预期失败检查。" }
}

function Invoke-EarlyFailureScenario {
    param([Parameter(Mandatory = $true)][string]$ScenarioName)
    $scenarioStartedAt = [DateTime]::UtcNow
    $package = $null
    $listener = $null
    $exitHookEntered = $false
    $allowedParent = $normalScenarioRoot
    $credentialSecret = $null
    try {
        Assert-KanyikanRuntimeAbsent
        if (@('DockerStopped', 'WindowsContainers', 'DiskInsufficient') -contains $ScenarioName) {
            foreach ($hookAction in @('Enter', 'Exit')) {
                $hookPath = [IO.Path]::Combine($hooksRoot, "$hookAction-$ScenarioName.ps1")
                if (-not [IO.File]::Exists($hookPath)) { throw "缺少 E2E 基础设施 Hook：$hookPath" }
            }
            $exitHookEntered = $true
            $hookMetadata = Invoke-ScenarioHook -Action 'Enter' -ScenarioName $ScenarioName
            if ($ScenarioName -ceq 'DiskInsufficient') {
                if ($null -eq $hookMetadata -or [string]::IsNullOrWhiteSpace([string]$hookMetadata.volumeRoot)) { throw 'DiskInsufficient Enter Hook 必须返回 volumeRoot。' }
                $allowedParent = [IO.Path]::GetFullPath([string]$hookMetadata.volumeRoot)
            }
        }
        $package = Expand-ScenarioPackage -ScenarioName $ScenarioName -ParentRoot $allowedParent
        switch ($ScenarioName) {
            'PortOccupied' {
                $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 10443)
                $listener.Start()
            }
            'ManifestTampered' {
                [IO.File]::AppendAllText(([IO.Path]::Combine($package.packageRoot, 'release-manifest.json')), "`n ", (New-Object Text.UTF8Encoding($false)))
            }
            'ImageArchiveCorrupt' {
                $stream = New-Object IO.FileStream(([IO.Path]::Combine($package.packageRoot, 'images', 'kanyikan-images-windows-amd64.tar')), [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try { $stream.WriteByte(75) }
                finally { $stream.Dispose() }
            }
        }
        $credentialSecret = New-KanyikanE2EAdminPassword
        $run = Invoke-ControllerForEvidence -ControllerPath $package.controllerPath -Command 'install' -CredentialSecret $credentialSecret
        $credentialSecret = $null
        Assert-ExpectedFailure -ScenarioName $ScenarioName -Run $run
        if ($ScenarioName -ceq 'PortOccupied' -and -not $listener.Server.IsBound) { throw '安装器结束了 10443 端口占用者。' }
        Assert-EarlyFailureHasNoPersistentSideEffects -PackageRoot $package.packageRoot
        return New-RunEvidence -ScenarioName $ScenarioName -ExpectedExitCode ([int]$expectedExitCodes[$ScenarioName]) -Run $run -ScenarioStartedAt $scenarioStartedAt
    }
    finally {
        $credentialSecret = $null
        try {
            if ($null -ne $listener) { $listener.Stop() }
            if ($null -ne $package) { Remove-ScenarioTree -Path $package.installParent -AllowedParent $allowedParent }
        }
        finally {
            try {
                if ($exitHookEntered) { Invoke-ScenarioHook -Action 'Exit' -ScenarioName $ScenarioName | Out-Null }
            }
            finally { Assert-KanyikanRuntimeAbsent }
        }
    }
}

function Wait-ForInstallState {
    param(
        [Parameter(Mandatory = $true)][string]$PackageRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedState,
        [Parameter(Mandatory = $true)][psobject]$Handle,
        [int]$TimeoutSeconds = 1200
    )
    $states = @('NEW', 'PREFLIGHT_OK', 'VERIFIED', 'IMAGES_LOADED', 'CONFIG_CREATED', 'CERT_READY', 'SERVICES_STARTING', 'HEALTHY', 'INSTALLED')
    $targetIndex = [Array]::IndexOf($states, $ExpectedState)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $statePath = [IO.Path]::Combine($PackageRoot, 'state', 'install-state.json')
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($Handle.process.HasExited) { throw "安装进程在到达 $ExpectedState 前退出：$($Handle.process.ExitCode)" }
        if ([IO.File]::Exists($statePath)) {
            try {
                $state = [IO.File]::ReadAllText($statePath, [Text.Encoding]::UTF8) | ConvertFrom-Json
                $currentState = [string]$state.currentState
                if ($currentState -ceq $ExpectedState) { return $currentState }
                if ([Array]::IndexOf($states, $currentState) -gt $targetIndex) { throw "未能在状态窗口内中断：目标=$ExpectedState，已到达=$currentState" }
            }
            catch [System.IO.IOException] { }
        }
        Start-Sleep -Milliseconds 1
    }
    throw "等待安装状态超时：$ExpectedState"
}

function Get-InstalledIdentityHashes {
    param([Parameter(Mandatory = $true)][string]$PackageRoot)
    return [pscustomobject][ordered]@{
        systemEnvironment = Get-KanyikanE2EFileSha256 -Path ([IO.Path]::Combine($PackageRoot, 'config', 'system.env'))
        leafPrivateKey = Get-KanyikanE2EFileSha256 -Path ([IO.Path]::Combine($PackageRoot, 'config', 'certs', 'localhost.key'))
        rootCertificate = Get-KanyikanE2EFileSha256 -Path ([IO.Path]::Combine($PackageRoot, 'config', 'certs', 'local-root-ca.crt'))
    }
}

function Invoke-InterruptionRetryStage {
    param([Parameter(Mandatory = $true)][string]$TargetState)
    $scenarioStartedAt = [DateTime]::UtcNow
    $package = $null
    $handle = $null
    $purgeCompleted = $false
    $credentialSecret = New-KanyikanE2EAdminPassword
    try {
        Assert-KanyikanRuntimeAbsent
        $package = Expand-ScenarioPackage -ScenarioName "InterruptionRetry-$TargetState" -ParentRoot $normalScenarioRoot
        $handle = Start-KanyikanE2EController -WrapperPath $wrapperPath -ControllerPath $package.controllerPath -Command 'install' -CredentialSecret $credentialSecret
        [void](Wait-ForInstallState -PackageRoot $package.packageRoot -ExpectedState $TargetState -Handle $handle)
        $interruptedResult = Stop-KanyikanE2EController -Handle $handle
        $handle = $null

        $retryResult = Invoke-ControllerForEvidence -ControllerPath $package.controllerPath -Command 'install' -CredentialSecret $credentialSecret
        if ([int]$retryResult.exitCode -ne 0) { throw "$TargetState 中断后的重试安装失败：$($retryResult.exitCode)" }
        Import-Module ([IO.Path]::Combine($package.packageRoot, 'lib', 'Kanyikan.Installer.psm1')) -Force
        Test-KanyikanAuthenticatedSmoke -InstallRoot $package.packageRoot

        $identityBefore = Get-InstalledIdentityHashes -PackageRoot $package.packageRoot
        $configSha256BeforeIdempotent = [string]$identityBefore.systemEnvironment
        $idempotentResult = Invoke-ControllerForEvidence -ControllerPath $package.controllerPath -Command 'install' -CredentialSecret $credentialSecret
        if ([int]$idempotentResult.exitCode -ne 0) { throw "$TargetState 恢复后重复安装不幂等：$($idempotentResult.exitCode)" }
        $identityAfter = Get-InstalledIdentityHashes -PackageRoot $package.packageRoot
        if ($configSha256BeforeIdempotent -cne [string]$identityAfter.systemEnvironment -or [string]$identityBefore.leafPrivateKey -cne [string]$identityAfter.leafPrivateKey -or [string]$identityBefore.rootCertificate -cne [string]$identityAfter.rootCertificate) {
            throw "$TargetState 恢复后的重复安装重置了配置、密钥或证书。"
        }

        $purgeResult = Invoke-ControllerForEvidence -ControllerPath $package.controllerPath -Command 'uninstall' -CredentialSecret $credentialSecret -PurgeData
        if ([int]$purgeResult.exitCode -ne 0) { throw "$TargetState 验收清理失败：$($purgeResult.exitCode)" }
        $purgeCompleted = $true
        Assert-KanyikanRuntimeAbsent
        return [pscustomobject][ordered]@{
            scenario = "InterruptionRetry/$TargetState"
            command = "install(kill@$TargetState)->install->install->uninstall --purge-data"
            expectedExitCode = 0
            actualExitCode = [int]$retryResult.exitCode
            interruptedProcessExitCode = [int]$interruptedResult.exitCode
            outputSha256 = [string]$retryResult.outputSha256
            idempotentOutputSha256 = [string]$idempotentResult.outputSha256
            identitySha256 = Get-KanyikanE2EStringSha256 -Value (($identityAfter | ConvertTo-Json -Compress))
            startedAt = $scenarioStartedAt.ToString('o')
            completedAt = [DateTime]::UtcNow.ToString('o')
            passed = $true
        }
    }
    finally {
        if ($null -ne $handle) {
            try { Stop-KanyikanE2EController -Handle $handle | Out-Null }
            catch { }
            $handle = $null
        }
        if ($null -ne $package -and -not $purgeCompleted) {
            try {
                $cleanupResult = Invoke-ControllerForEvidence -ControllerPath $package.controllerPath -Command 'uninstall' -CredentialSecret $credentialSecret -PurgeData
                $purgeCompleted = [int]$cleanupResult.exitCode -eq 0
            }
            catch { }
        }
        $credentialSecret = $null
        Assert-KanyikanRuntimeAbsent
        if ($null -ne $package) { Remove-ScenarioTree -Path $package.installParent -AllowedParent $normalScenarioRoot }
    }
}

[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($evidenceFullPath)) | Out-Null
[IO.Directory]::CreateDirectory($normalScenarioRoot) | Out-Null
$offlineZipSha256 = Get-KanyikanE2EFileSha256 -Path $offlineZipPath
$sourceRoot = [IO.Path]::GetFullPath([IO.Path]::Combine($PSScriptRoot, '..', '..', '..'))
$sourceCommit = (& git -C $sourceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') { throw '无法记录 E2E 源码 commit。' }
$dockerVersion = Get-ToolVersionForEvidence -Arguments @('version', '--format', '{{.Server.Version}}')
$composeVersion = Get-ToolVersionForEvidence -Arguments @('compose', 'version', '--short')
$windowsVersion = [Environment]::OSVersion.VersionString
$releaseVersion = Get-ReleaseVersionFromOfflineZip

try {
    foreach ($scenarioName in $Scenario) {
        if ($scenarioName -ceq 'InterruptionRetry') {
            foreach ($targetState in @('IMAGES_LOADED', 'CONFIG_CREATED', 'SERVICES_STARTING')) {
                $result = Invoke-InterruptionRetryStage -TargetState $targetState
                $results.Add($result)
            }
        }
        else {
            $result = Invoke-EarlyFailureScenario -ScenarioName $scenarioName
            $results.Add($result)
        }
    }
}
catch {
    $terminalFailure = Get-KanyikanE2EStringSha256 -Value $_.Exception.Message
}
finally {
    $evidence = [pscustomobject][ordered]@{
        schemaVersion = 1
        sourceCommit = $sourceCommit
        releaseVersion = $releaseVersion
        offlineZipSha256 = $offlineZipSha256
        publicKeySha256 = $ExpectedPublicKeySha256
        windowsVersion = $windowsVersion
        powerShellVersion = $PSVersionTable.PSVersion.ToString()
        dockerVersion = $dockerVersion
        composeVersion = $composeVersion
        startedAt = $startedAt.ToString('o')
        completedAt = [DateTime]::UtcNow.ToString('o')
        scenarios = @($results)
        passed = $null -eq $terminalFailure -and $results.Count -gt 0
        failureSha256 = $terminalFailure
    }
    $temporaryEvidence = "$evidenceFullPath.tmp-$([Guid]::NewGuid().ToString('N'))"
    [IO.File]::WriteAllText($temporaryEvidence, ($evidence | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporaryEvidence -Destination $evidenceFullPath -Force
}

if ($null -ne $terminalFailure) { throw "Windows 负向 E2E 失败；详情摘要=$terminalFailure；证据=$evidenceFullPath" }
Write-Host "Windows 负向 E2E 通过；证据=$evidenceFullPath"
