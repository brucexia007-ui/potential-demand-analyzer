$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$modulePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\Kanyikan.Installer.psm1'
Import-Module $modulePath -Force
$script:Passed = 0
$script:Failed = 0

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Invoke-TestCase {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passed++; Write-Host "PASS $Name" }
    catch { $script:Failed++; Write-Host "FAIL $Name - $($_.Exception.Message)" }
}

function New-ImageFixture {
    $images = [ordered]@{}
    $inspected = @()
    $index = 0
    foreach ($name in @('backend', 'frontend', 'postgres', 'redis', 'nginx', 'browserless')) {
        $hex = ([string]($index + 1)) * 64
        $reference = "registry.local/kanyikan/$name@sha256:$hex"
        $imageId = 'sha256:' + ([string]($index + 7)) * 64
        $images[$name] = [pscustomobject]@{ reference = $reference; imageId = $imageId }
        $inspected += [pscustomobject]@{ reference = $reference; imageId = $imageId; os = 'linux'; architecture = 'amd64'; repoDigests = @($reference) }
        $index++
    }
    return [pscustomobject]@{
        manifest = [pscustomobject]@{ images = [pscustomobject]$images }
        references = @($images.Values | ForEach-Object { $_.reference })
        inspected = $inspected
    }
}

Invoke-TestCase '恰好六个声明镜像且身份平台匹配时通过' {
    $fixture = New-ImageFixture
    $result = Test-KanyikanLoadedImageFacts -Manifest $fixture.manifest -LoadedReferences $fixture.references -InspectedImages $fixture.inspected
    Assert-True ($result.Count -eq 6) '验证结果不是六个镜像。'
    Assert-True (@($result | Where-Object { $_.platform -cne 'linux/amd64' }).Count -eq 0) '平台结果错误。'
}

Invoke-TestCase '缺少声明镜像时拒绝' {
    $fixture = New-ImageFixture
    $threw = $false
    try { Test-KanyikanLoadedImageFacts -Manifest $fixture.manifest -LoadedReferences @($fixture.references | Select-Object -First 5) -InspectedImages $fixture.inspected | Out-Null } catch { $threw = $_.Exception.Message.Contains('恰好加载 6 个') }
    Assert-True $threw '缺少镜像未被拒绝。'
}

Invoke-TestCase '包含未声明镜像时拒绝' {
    $fixture = New-ImageFixture
    $references = @($fixture.references | Select-Object -First 5) + @('registry.local/evil@sha256:' + ('f' * 64))
    $threw = $false
    try { Test-KanyikanLoadedImageFacts -Manifest $fixture.manifest -LoadedReferences $references -InspectedImages $fixture.inspected | Out-Null } catch { $threw = $_.Exception.Message.Contains('未声明镜像') }
    Assert-True $threw '未声明镜像未被拒绝。'
}

Invoke-TestCase '镜像 ID 漂移时拒绝' {
    $fixture = New-ImageFixture
    $fixture.inspected[0].imageId = 'sha256:' + ('e' * 64)
    $threw = $false
    try { Test-KanyikanLoadedImageFacts -Manifest $fixture.manifest -LoadedReferences $fixture.references -InspectedImages $fixture.inspected | Out-Null } catch { $threw = $_.Exception.Message.Contains('镜像 ID') }
    Assert-True $threw '镜像 ID 漂移未被拒绝。'
}

Invoke-TestCase '镜像平台错误时拒绝' {
    $fixture = New-ImageFixture
    $fixture.inspected[0].architecture = 'arm64'
    $threw = $false
    try { Test-KanyikanLoadedImageFacts -Manifest $fixture.manifest -LoadedReferences $fixture.references -InspectedImages $fixture.inspected | Out-Null } catch { $threw = $_.Exception.Message.Contains('linux/amd64') }
    Assert-True $threw '错误镜像平台未被拒绝。'
}

Invoke-TestCase 'RepoDigest 漂移时拒绝' {
    $fixture = New-ImageFixture
    $fixture.inspected[0].repoDigests = @('registry.local/other@sha256:' + ('a' * 64))
    $threw = $false
    try { Test-KanyikanLoadedImageFacts -Manifest $fixture.manifest -LoadedReferences $fixture.references -InspectedImages $fixture.inspected | Out-Null } catch { $threw = $_.Exception.Message.Contains('RepoDigest') }
    Assert-True $threw 'RepoDigest 漂移未被拒绝。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
