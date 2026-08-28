Set-StrictMode -Version 2.0

function Assert-KanyikanDedicatedE2EHost {
    if ([Environment]::GetEnvironmentVariable('KANYIKAN_CLEAN_E2E', 'Machine') -cne '1') {
        throw '只允许在设置机器级 KANYIKAN_CLEAN_E2E=1 的专用一次性 runner 上执行。'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Windows 发行 E2E 必须以管理员身份运行。'
    }
}

function New-KanyikanE2EAdminPassword {
    $random = New-Object byte[] 24
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($random) }
    finally { $generator.Dispose() }
    return "E2E-$([Convert]::ToBase64String($random).Replace('/', '_').Replace('+', '-'))!aA1"
}

function Get-KanyikanE2EStringSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-KanyikanE2EFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Write-KanyikanE2EControllerOutput {
    param(
        [Parameter(Mandatory = $true)][psobject]$Run,
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')][string]$Name
    )
    foreach ($property in @('stdout', 'stderr', 'outputSha256')) {
        if ($Run.PSObject.Properties.Name -cnotcontains $property) { throw "控制器运行记录缺少字段：$property" }
    }
    $text = [string]$Run.stdout + "`n" + [string]$Run.stderr
    $expectedSha256 = [string]$Run.outputSha256
    if ($expectedSha256 -notmatch '^[0-9a-f]{64}$' -or (Get-KanyikanE2EStringSha256 -Value $text) -cne $expectedSha256) {
        throw '控制器输出摘要与运行记录不一致。'
    }
    foreach ($pattern in @(
        '(?im)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----',
        '(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}',
        '(?i)\b(?:ADMIN_PASSWORD|SECRET_KEY|CONFIG_ENCRYPTION_KEY|POSTGRES_PASSWORD|REDIS_PASSWORD|BROWSERLESS_TOKEN|API[_-]?KEY|PASSWORD|TOKEN|SECRET)\s*[:=]\s*[^\s;]{4,}',
        '(?i)https?://[^\s/:@]+:[^\s/@]+@',
        '(?i)\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b',
        '(?im)^\s*(?:Cookie|Set-Cookie)\s*:'
    )) {
        if ($text -match $pattern) { throw '控制器输出包含疑似秘密，拒绝写入 E2E 证据。' }
    }

    $directory = [IO.Path]::GetFullPath($OutputDirectory)
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    if (([IO.File]::GetAttributes($directory) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'E2E 输出目录不得是重解析点。' }
    $destination = [IO.Path]::Combine($directory, "$Name.log")
    if ([IO.File]::Exists($destination)) { throw "E2E 输出证据已存在，拒绝覆盖：$Name" }
    $temporary = [IO.Path]::Combine($directory, ".$Name.tmp-$([Guid]::NewGuid().ToString('N'))")
    try {
        [IO.File]::WriteAllText($temporary, $text, (New-Object Text.UTF8Encoding($false)))
        if ((Get-KanyikanE2EFileSha256 -Path $temporary) -cne $expectedSha256) { throw 'E2E 输出落盘后的摘要复核失败。' }
        [IO.File]::Move($temporary, $destination)
    }
    finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
    return [pscustomobject][ordered]@{
        path = $destination
        sha256 = $expectedSha256
        sizeBytes = [IO.FileInfo]::new($destination).Length
    }
}

function Start-KanyikanE2EController {
    param(
        [Parameter(Mandatory = $true)][string]$WrapperPath,
        [Parameter(Mandatory = $true)][string]$ControllerPath,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$CredentialSecret,
        [string]$Package,
        [string]$Backup,
        [switch]$PurgeData
    )
    foreach ($value in @($WrapperPath, $ControllerPath, $Package, $Backup)) {
        if (-not [string]::IsNullOrEmpty($value) -and $value.Contains('"')) { throw 'E2E 路径不得包含双引号。' }
    }
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$WrapperPath`"", '-ControllerPath', "`"$ControllerPath`"", '-Command', $Command)
    if (-not [string]::IsNullOrWhiteSpace($Package)) { $arguments += @('-Package', "`"$Package`"") }
    if (-not [string]::IsNullOrWhiteSpace($Backup)) { $arguments += @('-Backup', "`"$Backup`"") }
    if ($PurgeData) { $arguments += '-PurgeData' }
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'powershell.exe'
    $startInfo.Arguments = $arguments -join ' '
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.EnvironmentVariables['KANYIKAN_E2E_ADMIN_PASSWORD'] = $CredentialSecret
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { $process.Dispose(); throw "无法启动控制器命令：$Command" }
    return [pscustomobject]@{
        command = $Command
        process = $process
        stdoutTask = $process.StandardOutput.ReadToEndAsync()
        stderrTask = $process.StandardError.ReadToEndAsync()
        adminPassword = $CredentialSecret
    }
}

function Complete-KanyikanE2EController {
    param(
        [Parameter(Mandatory = $true)][psobject]$Handle,
        [int[]]$AllowedExitCodes = @(0)
    )
    try {
        $Handle.process.WaitForExit()
        $stdout = $Handle.stdoutTask.Result
        $stderr = $Handle.stderrTask.Result
        if ($stdout.Contains($Handle.adminPassword) -or $stderr.Contains($Handle.adminPassword)) { throw "控制器输出泄露 E2E 管理员密码：$($Handle.command)" }
        $result = [pscustomobject][ordered]@{
            command = [string]$Handle.command
            exitCode = [int]$Handle.process.ExitCode
            outputSha256 = Get-KanyikanE2EStringSha256 -Value ($stdout + "`n" + $stderr)
            stdout = $stdout
            stderr = $stderr
        }
        if ($AllowedExitCodes -notcontains $result.exitCode) { throw "控制器命令失败：$($Handle.command)；退出码=$($result.exitCode)" }
        return $result
    }
    finally {
        $Handle.adminPassword = $null
        $Handle.process.Dispose()
    }
}

function Stop-KanyikanE2EController {
    param([Parameter(Mandatory = $true)][psobject]$Handle)
    if (-not $Handle.process.HasExited) { $Handle.process.Kill(); $Handle.process.WaitForExit() }
    return Complete-KanyikanE2EController -Handle $Handle -AllowedExitCodes @($Handle.process.ExitCode)
}

function Invoke-KanyikanE2EController {
    param(
        [Parameter(Mandatory = $true)][string]$WrapperPath,
        [Parameter(Mandatory = $true)][string]$ControllerPath,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$CredentialSecret,
        [string]$Package,
        [string]$Backup,
        [switch]$PurgeData,
        [int[]]$AllowedExitCodes = @(0)
    )
    $handle = Start-KanyikanE2EController -WrapperPath $WrapperPath -ControllerPath $ControllerPath -Command $Command -CredentialSecret $CredentialSecret -Package $Package -Backup $Backup -PurgeData:$PurgeData
    return Complete-KanyikanE2EController -Handle $handle -AllowedExitCodes $AllowedExitCodes
}

function Invoke-KanyikanE2EGuard {
    param([Parameter(Mandatory = $true)][string]$Path)
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Path
    if ($LASTEXITCODE -ne 0) { throw "E2E 基础设施守卫脚本失败：$Path；退出码=$LASTEXITCODE" }
}

Export-ModuleMember -Function @(
    'Assert-KanyikanDedicatedE2EHost',
    'Complete-KanyikanE2EController',
    'Get-KanyikanE2EFileSha256',
    'Get-KanyikanE2EStringSha256',
    'Invoke-KanyikanE2EController',
    'Invoke-KanyikanE2EGuard',
    'New-KanyikanE2EAdminPassword',
    'Start-KanyikanE2EController',
    'Stop-KanyikanE2EController',
    'Write-KanyikanE2EControllerOutput'
)
