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

$digestImage = 'registry.local/kanyikan/backend@sha256:' + ('a' * 64)
$certificateDirectory = Join-Path ([IO.Path]::GetTempPath()) '看一看 TLS, 空格'

Invoke-TestCase '证书生成容器固定镜像且完全离线' {
    $arguments = @(Get-KanyikanCertificateDockerArguments -BackendImage $digestImage -CertificateDirectory $certificateDirectory -LeafValidityDays 365 -CaValidityDays 1825)
    $joined = $arguments -join ' '
    Assert-True ($joined.Contains('--pull never')) '未禁止拉取镜像。'
    Assert-True ($joined.Contains('--platform linux/amd64')) '未固定镜像平台。'
    Assert-True ($joined.Contains('--network none')) '证书容器仍可访问网络。'
    Assert-True ($joined.Contains('--read-only')) '证书容器根文件系统不是只读。'
    Assert-True ($joined.Contains('--cap-drop ALL')) '证书容器未移除 capabilities。'
    Assert-True ($joined.Contains('--security-opt no-new-privileges')) '证书容器未禁止提权。'
    Assert-True ($joined.Contains($digestImage)) '证书容器未使用指定 digest 镜像。'
    Assert-True (@($arguments | Where-Object { $_ -ceq "${certificateDirectory}:/certs:rw" }).Count -eq 1) '中文、空格或逗号证书路径未作为单个挂载参数保留。'
}

Invoke-TestCase '证书复核只读挂载且启用 validate' {
    $arguments = @(Get-KanyikanCertificateDockerArguments -BackendImage $digestImage -CertificateDirectory $certificateDirectory -LeafValidityDays 30 -CaValidityDays 825 -Validate)
    Assert-True (@($arguments | Where-Object { $_ -ceq "${certificateDirectory}:/certs:ro" }).Count -eq 1) '复核挂载不是只读。'
    Assert-True ($arguments[-1] -ceq '--validate') '复核未调用 validate 模式。'
}

Invoke-TestCase '拒绝非 digest Backend 镜像' {
    $threw = $false
    try { Get-KanyikanCertificateDockerArguments -BackendImage 'kanyikan/backend:latest' -CertificateDirectory $certificateDirectory -LeafValidityDays 30 -CaValidityDays 825 | Out-Null } catch { $threw = $_.Exception.Message.Contains('digest') }
    Assert-True $threw '标签镜像错误通过。'
}

Invoke-TestCase '拒绝 CA 有效期不晚于叶子证书' {
    $threw = $false
    try { Get-KanyikanCertificateDockerArguments -BackendImage $digestImage -CertificateDirectory $certificateDirectory -LeafValidityDays 825 -CaValidityDays 825 | Out-Null } catch { $threw = $_.Exception.Message.Contains('晚于') }
    Assert-True $threw '相同有效期错误通过。'
}

Invoke-TestCase '拒绝信任时不读取或写入证书存储' {
    $result = Install-KanyikanLocalRootTrust -CertificatePath 'Z:\不存在\local-root-ca.crt' -Consent $false
    Assert-True ($null -eq $result) '拒绝信任后仍返回 Thumbprint。'
}

Invoke-TestCase '删除信任只接受精确 SHA1 Thumbprint' {
    $threw = $false
    try { Remove-KanyikanLocalRootTrust -Thumbprint 'ABC*' | Out-Null } catch { $threw = $true }
    Assert-True $threw '通配 Thumbprint 错误通过。'
}

Invoke-TestCase '证书存储范围仅为 CurrentUser Root' {
    $source = [IO.File]::ReadAllText($modulePath, [Text.Encoding]::UTF8)
    Assert-True ($source.Contains('[Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser')) '未使用 CurrentUser 证书存储。'
    Assert-True (-not $source.Contains('[Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine')) '代码包含 LocalMachine 根证书写入。'
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) { exit 1 }
