$ErrorActionPreference = 'Stop'
$modulePath = Join-Path $PSScriptRoot '..\lib\Kanyikan.Installer.psm1'
Import-Module $modulePath -Force

$script:Passed = 0
$script:Failed = 0

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Invoke-TestCase([string]$Name, [scriptblock]$Body) {
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}

function New-Zip {
    param([string]$Path, [hashtable]$Entries)
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::Open($Path, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($entryName in $Entries.Keys) {
            $entry = $archive.CreateEntry($entryName)
            $writer = New-Object System.IO.StreamWriter($entry.Open(), (New-Object Text.UTF8Encoding($false)))
            try { $writer.Write([string]$Entries[$entryName]) }
            finally { $writer.Dispose() }
        }
    }
    finally { $archive.Dispose() }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) "kanyikan-update-package-$([Guid]::NewGuid().ToString('N'))"
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
try {
    Invoke-TestCase '语义化版本按规范比较' {
        Assert-True ((Compare-KanyikanSemanticVersion -Left '1.0.0' -Right '1.0.0-rc.1') -gt 0) '正式版应高于预发行版。'
        Assert-True ((Compare-KanyikanSemanticVersion -Left '1.0.0-rc.10' -Right '1.0.0-rc.2') -gt 0) '数字预发行标识比较错误。'
        Assert-True ((Compare-KanyikanSemanticVersion -Left '2.0.0+build.2' -Right '2.0.0+build.1') -eq 0) '构建元数据不应影响优先级。'
        $threw = $false
        try { Compare-KanyikanSemanticVersion -Left '1.0.0-01' -Right '1.0.0' | Out-Null } catch { $threw = $_.Exception.Message.Contains('语义化版本') }
        Assert-True $threw '带前导零的数字预发行标识未被拒绝。'
    }

    Invoke-TestCase '只接受严格递增且显式支持的升级路径' {
        $manifest = [pscustomobject]@{ release = [pscustomobject]@{ version = '1.1.0' }; upgrade = [pscustomobject]@{ supportedFrom = @('1.0.0'); migration = [pscustomobject]@{ strategy = 'alembic_upgrade_head' } } }
        $result = Test-KanyikanUpgradePath -CurrentVersion '1.0.0' -Manifest $manifest
        Assert-True ($result.targetVersion -ceq '1.1.0') '目标版本错误。'
        $manifest.upgrade.supportedFrom = @('0.9.0')
        $threw = $false
        try { Test-KanyikanUpgradePath -CurrentVersion '1.0.0' -Manifest $manifest | Out-Null } catch { $threw = $_.Exception.Message.Contains('不支持') }
        Assert-True $threw '未声明的升级来源未被拒绝。'
        $manifest.release.version = '1.0.0+rebuild'
        $manifest.upgrade.supportedFrom = @('1.0.0')
        $threw = $false
        try { Test-KanyikanUpgradePath -CurrentVersion '1.0.0' -Manifest $manifest | Out-Null } catch { $threw = $_.Exception.Message.Contains('严格高于') }
        Assert-True $threw '仅构建元数据变化的版本未被拒绝。'
    }

    Invoke-TestCase '安全解压唯一顶层发行目录' {
        $zipPath = Join-Path $testRoot 'valid.zip'
        New-Zip -Path $zipPath -Entries @{ 'Kanyikan-1.1.0/VERSION' = '1.1.0'; 'Kanyikan-1.1.0/docs/readme.txt' = 'ok' }
        $destination = Join-Path $testRoot 'valid-stage'
        $packageRoot = Expand-KanyikanUpdatePackage -ZipPath $zipPath -DestinationRoot $destination
        Assert-True ($packageRoot -ceq (Join-Path $destination 'Kanyikan-1.1.0')) '返回的发行根目录错误。'
        Assert-True ([IO.File]::Exists((Join-Path $packageRoot 'VERSION'))) '未解压发行文件。'
    }

    Invoke-TestCase '目录穿越 ZIP 在写入前被拒绝' {
        $zipPath = Join-Path $testRoot 'traversal.zip'
        New-Zip -Path $zipPath -Entries @{ 'Kanyikan-1.1.0/VERSION' = '1.1.0'; '../escaped.txt' = 'bad' }
        $destination = Join-Path $testRoot 'traversal-stage'
        $threw = $false
        try { Expand-KanyikanUpdatePackage -ZipPath $zipPath -DestinationRoot $destination | Out-Null } catch { $threw = $_.Exception.Message.Contains('非法相对路径') }
        Assert-True $threw '目录穿越 ZIP 未被拒绝。'
        Assert-True (-not [IO.Directory]::Exists($destination)) '失败的 ZIP 留下了暂存目录。'
        Assert-True (-not [IO.File]::Exists((Join-Path $testRoot 'escaped.txt'))) 'ZIP 写出了目标目录。'
    }

    Invoke-TestCase '多个顶层目录的 ZIP 被拒绝' {
        $zipPath = Join-Path $testRoot 'multiple-roots.zip'
        New-Zip -Path $zipPath -Entries @{ 'Kanyikan-1.1.0/VERSION' = '1.1.0'; 'other/file.txt' = 'bad' }
        $destination = Join-Path $testRoot 'multiple-roots-stage'
        $threw = $false
        try { Expand-KanyikanUpdatePackage -ZipPath $zipPath -DestinationRoot $destination | Out-Null } catch { $threw = $_.Exception.Message.Contains('一个顶层') }
        Assert-True $threw '多个顶层目录未被拒绝。'
        Assert-True (-not [IO.Directory]::Exists($destination)) '失败的 ZIP 留下了暂存目录。'
    }

    Invoke-TestCase '发行资产可切换并从快照完整回滚' {
        $installRoot = Join-Path $testRoot 'asset-install'
        $packageRoot = Join-Path $testRoot 'asset-package'
        [IO.Directory]::CreateDirectory((Join-Path $installRoot 'config')) | Out-Null
        [IO.Directory]::CreateDirectory((Join-Path $installRoot 'docs')) | Out-Null
        [IO.Directory]::CreateDirectory((Join-Path $packageRoot 'docs')) | Out-Null
        $controlPaths = @('release-manifest.json', 'release-manifest.sig', 'manifest.sha256')
        $oldFiles = @('compose.release.yml', 'docs/old.txt')
        $newFiles = @('compose.release.yml', 'docs/new.txt')
        foreach ($relativePath in $controlPaths + $oldFiles) {
            [IO.File]::WriteAllText((Join-Path $installRoot $relativePath), "old:$relativePath")
        }
        foreach ($relativePath in $controlPaths + $newFiles) {
            [IO.File]::WriteAllText((Join-Path $packageRoot $relativePath), "new:$relativePath")
        }
        [IO.File]::WriteAllText((Join-Path $installRoot 'config/system.env'), 'LOCAL_SECRET=preserved')
        $oldManifest = [pscustomobject]@{ files = @($oldFiles | ForEach-Object { [pscustomobject]@{ path = $_ } }) }
        $newManifest = [pscustomobject]@{ files = @($newFiles | ForEach-Object { [pscustomobject]@{ path = $_ } }) }
        $snapshotRoot = Join-Path $installRoot 'state/update-transactions/tx-1/assets'

        New-KanyikanReleaseAssetSnapshot -InstallRoot $installRoot -CurrentManifest $oldManifest -SnapshotRoot $snapshotRoot | Out-Null
        Set-KanyikanReleaseAssets -InstallRoot $installRoot -PackageRoot $packageRoot -CurrentManifest $oldManifest -NewManifest $newManifest
        Assert-True ([IO.File]::ReadAllText((Join-Path $installRoot 'compose.release.yml')).StartsWith('new:')) '未安装新 Compose。'
        Assert-True (-not [IO.File]::Exists((Join-Path $installRoot 'docs/old.txt'))) '旧版独有资产未移除。'
        Assert-True ([IO.File]::Exists((Join-Path $installRoot 'docs/new.txt'))) '新版独有资产未安装。'
        Assert-True ([IO.File]::ReadAllText((Join-Path $installRoot 'config/system.env')) -ceq 'LOCAL_SECRET=preserved') '本机配置被覆盖。'

        Restore-KanyikanReleaseAssets -InstallRoot $installRoot -SnapshotRoot $snapshotRoot -PreviousManifest $oldManifest -FailedManifest $newManifest
        Assert-True ([IO.File]::ReadAllText((Join-Path $installRoot 'compose.release.yml')).StartsWith('old:')) '未恢复旧 Compose。'
        Assert-True ([IO.File]::Exists((Join-Path $installRoot 'docs/old.txt'))) '旧版独有资产未恢复。'
        Assert-True (-not [IO.File]::Exists((Join-Path $installRoot 'docs/new.txt'))) '失败版本独有资产未移除。'
        Assert-True ([IO.File]::ReadAllText((Join-Path $installRoot 'config/system.env')) -ceq 'LOCAL_SECRET=preserved') '回滚修改了本机配置。'

        Remove-KanyikanReleaseAssetSnapshot -InstallRoot $installRoot -SnapshotRoot $snapshotRoot
        Assert-True (-not [IO.Directory]::Exists($snapshotRoot)) '成功清理快照失败。'
    }

    Invoke-TestCase '更新镜像引用时保留全部本机密钥' {
        $environmentPath = Join-Path $testRoot 'system.env'
        $requiredSecrets = [ordered]@{
            SECRET_KEY = 'secret'; CONFIG_ENCRYPTION_KEY = 'encrypt'; ADMIN_PASSWORD = 'Password-2026!$\"';
            POSTGRES_PASSWORD = 'postgres'; REDIS_PASSWORD = 'redis'; BROWSERLESS_TOKEN = 'browserless';
            DATABASE_URL = 'postgresql://user:password@postgres/db'; REDIS_URL = 'redis://:password@redis/0'
        }
        $imageNames = @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless')
        $oldImages = [ordered]@{}
        $newImages = [ordered]@{}
        $lines = @()
        foreach ($imageName in $imageNames) {
            $oldReference = "registry.local/$imageName@sha256:$('a' * 64)"
            $newReference = "registry.local/$imageName@sha256:$('b' * 64)"
            $oldImages[$imageName] = [pscustomobject]@{ reference = $oldReference }
            $newImages[$imageName] = [pscustomobject]@{ reference = $newReference }
            $lines += "$($imageName.ToUpperInvariant())_IMAGE=`"$oldReference`""
        }
        foreach ($key in $requiredSecrets.Keys) {
            $encoded = ([string]$requiredSecrets[$key]).Replace('\', '\\').Replace('"', '\"').Replace('$', '$$')
            $lines += "$key=`"$encoded`""
        }
        [IO.File]::WriteAllLines($environmentPath, $lines, (New-Object Text.UTF8Encoding($false)))
        Set-KanyikanRestrictedFileAcl -Path $environmentPath
        $oldManifest = [pscustomobject]@{ images = [pscustomobject]$oldImages }
        $newManifest = [pscustomobject]@{ images = [pscustomobject]$newImages }

        Update-KanyikanSystemImageReferences -Path $environmentPath -CurrentManifest $oldManifest -NewManifest $newManifest
        $updated = [IO.File]::ReadAllText($environmentPath)
        foreach ($imageName in $imageNames) { Assert-True $updated.Contains("sha256:$('b' * 64)") '未写入新镜像引用。' }
        foreach ($key in $requiredSecrets.Keys) {
            $encoded = ([string]$requiredSecrets[$key]).Replace('\', '\\').Replace('"', '\"').Replace('$', '$$')
            Assert-True $updated.Contains("$key=`"$encoded`"") "本机密钥 $key 被修改。"
        }
    }

    Invoke-TestCase 'Alembic 迁移命令固定使用新 Backend 且禁止拉取' {
        $arguments = @(Get-KanyikanMigrationArguments -InstallRoot 'C:\Kanyikan' -Strategy 'alembic_upgrade_head')
        $tail = @('run', '--rm', '--no-deps', '--pull', 'never', '--entrypoint', 'alembic', 'backend', 'upgrade', 'head')
        Assert-True (($arguments[-$tail.Count..-1] -join '|') -ceq ($tail -join '|')) 'Alembic 迁移命令不符合契约。'
        Assert-True (@(Get-KanyikanMigrationArguments -InstallRoot 'C:\Kanyikan' -Strategy 'none').Count -eq 0) 'none 策略不应执行迁移命令。'
    }
}
finally {
    if ([IO.Directory]::Exists($testRoot)) { [IO.Directory]::Delete($testRoot, $true) }
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
