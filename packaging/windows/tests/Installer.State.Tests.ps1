$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$modulePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'lib\Kanyikan.Installer.psm1'
Import-Module $modulePath -Force

$script:Passed = 0
$script:Failed = 0

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-TestCase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Body
    )

    try {
        & $Body
        $script:Passed++
        Write-Host "PASS $Name"
    }
    catch {
        $script:Failed++
        Write-Host "FAIL $Name - $($_.Exception.Message)"
    }
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) "kanyikan-state-$([Guid]::NewGuid().ToString('N'))"
[System.IO.Directory]::CreateDirectory($testRoot) | Out-Null

try {
    Invoke-TestCase '不存在状态文件时返回内存中的 NEW，不产生副作用' {
        $root = Join-Path $testRoot '中文 路径'
        [System.IO.Directory]::CreateDirectory($root) | Out-Null
        $state = Read-KanyikanInstallState -InstallRoot $root
        Assert-True ($state.currentState -ceq 'NEW') '初始状态不是 NEW。'
        Assert-True (-not [System.IO.File]::Exists((Get-KanyikanStatePath -InstallRoot $root))) '读取初始状态产生了持久副作用。'
    }

    Invoke-TestCase '只允许顺序迁移并原子写入状态' {
        $root = Join-Path $testRoot 'sequential'
        [System.IO.Directory]::CreateDirectory($root) | Out-Null
        $state = New-KanyikanInstallState -InstallRoot $root
        foreach ($nextState in (Get-KanyikanInstallStates | Select-Object -Skip 1)) {
            $state = Set-KanyikanInstallState -State $state -NextState $nextState -InstallRoot $root
            $persisted = Read-KanyikanInstallState -InstallRoot $root
            Assert-True ($persisted.currentState -ceq $nextState) "未持久化状态 $nextState。"
        }
        $temporaryFiles = [System.IO.Directory]::GetFiles((Join-Path $root 'state'), '*.tmp')
        Assert-True ($temporaryFiles.Count -eq 0) '原子写入遗留临时文件。'
    }

    Invoke-TestCase '拒绝跳过或回退状态' {
        $root = Join-Path $testRoot 'illegal-transition'
        [System.IO.Directory]::CreateDirectory($root) | Out-Null
        $state = New-KanyikanInstallState -InstallRoot $root
        $threw = $false
        try {
            Set-KanyikanInstallState -State $state -NextState 'VERIFIED' -InstallRoot $root | Out-Null
        }
        catch {
            $threw = $true
        }
        Assert-True $threw '跳过状态未被拒绝。'
        Assert-True (-not [System.IO.File]::Exists((Get-KanyikanStatePath -InstallRoot $root))) '非法迁移写入了状态。'
    }

    Invoke-TestCase '失败保留最后成功状态并脱敏' {
        $root = Join-Path $testRoot 'failure'
        [System.IO.Directory]::CreateDirectory($root) | Out-Null
        $state = New-KanyikanInstallState -InstallRoot $root
        $state = Set-KanyikanInstallState -State $state -NextState 'PREFLIGHT_OK' -InstallRoot $root
        $reason = 'POSTGRES_PASSWORD=top-secret Bearer abc.def SECRET_KEY:another-secret https://user:pwd@example.test/path'
        $state = Set-KanyikanInstallFailure -State $state -Command 'install' -Stage 'VERIFIED' -ExitCode 30 -Reason $reason -InstallRoot $root
        $persisted = Read-KanyikanInstallState -InstallRoot $root
        $serialized = $persisted | ConvertTo-Json -Depth 12
        Assert-True ($persisted.currentState -ceq 'PREFLIGHT_OK') '失败错误推进了状态。'
        Assert-True ($persisted.lastFailure.exitCode -eq 30) '失败退出码未记录。'
        Assert-True ($serialized.Contains('[REDACTED]')) '敏感信息未标记为脱敏。'
        Assert-True (-not $serialized.Contains('top-secret')) '密码泄露到状态文件。'
        Assert-True (-not $serialized.Contains('abc.def')) 'Bearer Token 泄露到状态文件。'
        Assert-True (-not $serialized.Contains('another-secret')) '系统密钥泄露到状态文件。'
        Assert-True (-not $serialized.Contains('user:pwd')) '认证 URL 泄露到状态文件。'
    }

    Invoke-TestCase '拒绝损坏状态和安装根目录不一致' {
        $root = Join-Path $testRoot 'corrupt'
        [System.IO.Directory]::CreateDirectory((Join-Path $root 'state')) | Out-Null
        $statePath = Get-KanyikanStatePath -InstallRoot $root
        [System.IO.File]::WriteAllText($statePath, '{broken json', [Text.Encoding]::UTF8)
        $threw = $false
        try {
            Read-KanyikanInstallState -InstallRoot $root | Out-Null
        }
        catch {
            $threw = $_.Exception.Message.Contains('安装状态损坏')
        }
        Assert-True $threw '损坏 JSON 未以状态损坏终止。'

        [System.IO.File]::Delete($statePath)
        $state = New-KanyikanInstallState -InstallRoot $root
        $state = Set-KanyikanInstallState -State $state -NextState 'PREFLIGHT_OK' -InstallRoot $root
        $otherRoot = Join-Path $testRoot 'other'
        [System.IO.Directory]::CreateDirectory((Join-Path $otherRoot 'state')) | Out-Null
        [System.IO.File]::Copy($statePath, (Get-KanyikanStatePath -InstallRoot $otherRoot))
        $threw = $false
        try {
            Read-KanyikanInstallState -InstallRoot $otherRoot | Out-Null
        }
        catch {
            $threw = $_.Exception.Message.Contains('安装根目录')
        }
        Assert-True $threw '安装根目录不一致未被拒绝。'
    }
}
finally {
    if ([System.IO.Directory]::Exists($testRoot)) {
        [System.IO.Directory]::Delete($testRoot, $true)
    }
}

Write-Host "RESULT passed=$script:Passed failed=$script:Failed"
if ($script:Failed -gt 0) {
    exit 1
}
