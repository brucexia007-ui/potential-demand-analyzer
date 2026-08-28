"""构建 Kanyikan Windows amd64 在线引导包。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
GITHUB_REPOSITORY = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


ONLINE_SCRIPT = r'''[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ReleaseBaseUrl = '__KANYIKAN_RELEASE_BASE_URL__'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not ('KanyikanOnlineSignature' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Security.Cryptography;

public static class KanyikanOnlineSignature
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

function Get-KanyikanDownloadUri {
    param([string]$BaseUrl, [string]$FileName)
    $base = [Uri]$BaseUrl
    if (-not $base.IsAbsoluteUri -or $base.Scheme -cne 'https' -or -not [string]::IsNullOrEmpty($base.UserInfo) -or -not [string]::IsNullOrEmpty($base.Query) -or -not [string]::IsNullOrEmpty($base.Fragment)) {
        throw 'ReleaseBaseUrl 必须是不含认证信息、查询或片段的 HTTPS URL。'
    }
    return [Uri]::new($base.AbsoluteUri.TrimEnd('/') + '/' + $FileName)
}

$version = '__KANYIKAN_VERSION__'
$offlineZipName = '__KANYIKAN_OFFLINE_ZIP_NAME__'
$expectedPublicKeySha256 = '__KANYIKAN_PUBLIC_KEY_SHA256__'
$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$publicKeyPath = Join-Path $packageRoot 'public-key.pem'
$actualPublicKeySha256 = (Get-FileHash -LiteralPath $publicKeyPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualPublicKeySha256 -cne $expectedPublicKeySha256) {
    throw '在线引导包的发行公钥指纹不匹配。'
}

$workingRoot = Join-Path ([IO.Path]::GetTempPath()) ('Kanyikan-online-' + [Guid]::NewGuid().ToString('N'))
$installerExitCode = 1
try {
    New-Item -ItemType Directory -Path $workingRoot -ErrorAction Stop | Out-Null
    foreach ($name in @('SHA256SUMS', 'SHA256SUMS.sig', $offlineZipName)) {
        $destination = Join-Path $workingRoot $name
        Invoke-WebRequest -Uri (Get-KanyikanDownloadUri -BaseUrl $ReleaseBaseUrl -FileName $name) -OutFile $destination -UseBasicParsing
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf) -or (Get-Item -LiteralPath $destination).Length -eq 0) {
            throw "下载结果不存在或为空：$name"
        }
    }

    $checksumPath = Join-Path $workingRoot 'SHA256SUMS'
    $signaturePath = Join-Path $workingRoot 'SHA256SUMS.sig'
    $checksumBytes = [IO.File]::ReadAllBytes($checksumPath)
    $signatureBytes = [IO.File]::ReadAllBytes($signaturePath)
    $publicKeyBytes = [IO.File]::ReadAllBytes($publicKeyPath)
    if (-not [KanyikanOnlineSignature]::Verify($publicKeyBytes, $checksumBytes, $signatureBytes)) {
        throw 'SHA256SUMS 的 RSA-SHA256 签名无效。'
    }

    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $checksumText = $utf8.GetString($checksumBytes)
    $escapedName = [Regex]::Escape($offlineZipName)
    $matchingLines = @($checksumText -split "`n" | Where-Object { $_ -match "^[0-9a-f]{64}  $escapedName`r?$" })
    if ($matchingLines.Count -ne 1) {
        throw 'SHA256SUMS 必须且只能声明一次目标离线包。'
    }
    $expectedOfflineSha256 = $matchingLines[0].Substring(0, 64)
    $offlineZipPath = Join-Path $workingRoot $offlineZipName
    $actualOfflineSha256 = (Get-FileHash -LiteralPath $offlineZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualOfflineSha256 -cne $expectedOfflineSha256) {
        throw '离线包 SHA256 与已签名摘要不匹配。'
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $extractRoot = Join-Path $workingRoot 'offline'
    [IO.Compression.ZipFile]::ExtractToDirectory($offlineZipPath, $extractRoot)
    $offlineRoot = Join-Path $extractRoot ("Kanyikan-v$version-windows-amd64")
    $installer = Join-Path $offlineRoot 'install.cmd'
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw '已验证离线包缺少 install.cmd。'
    }
    Push-Location $offlineRoot
    try {
        & $installer
        $installerExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    if (Test-Path -LiteralPath $workingRoot) {
        Remove-Item -LiteralPath $workingRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

exit $installerExitCode
'''


def _parse_published_at(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("published-at 必须是 ISO 8601 时间。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("published-at 必须明确使用 UTC。")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_public_key(path: Path) -> bytes:
    public_key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise ValueError("在线引导包必须使用至少 3072 位 RSA 发行公钥。")
    return public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.PKCS1)


def _write_zip(root: Path, destination: Path, published_at: datetime) -> None:
    timestamp = published_at.replace(tzinfo=None)
    if timestamp.year < 1980:
        raise ValueError("ZIP 时间不得早于 1980 年。")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(root.iterdir(), key=lambda item: item.name):
            info = zipfile.ZipInfo(source.relative_to(root.parent).as_posix(), timestamp.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_online_release(
    *,
    output_directory: Path,
    version: str,
    published_at: str,
    public_key_path: Path,
    repository_url: str,
) -> dict[str, str]:
    if not STABLE_VERSION.fullmatch(version):
        raise ValueError("在线引导包版本必须是稳定版 X.Y.Z。")
    if not GITHUB_REPOSITORY.fullmatch(repository_url):
        raise ValueError("GitHub 仓库 URL 必须使用 https://github.com/owner/repository。")
    published = _parse_published_at(published_at)
    public_key = _canonical_public_key(public_key_path)
    public_key_sha256 = hashlib.sha256(public_key).hexdigest()

    output_directory = output_directory.resolve()
    package_name = f"Kanyikan-v{version}-windows-amd64-online"
    package_root = output_directory / package_name
    online_zip = output_directory / f"{package_name}.zip"
    if package_root.exists() or online_zip.exists():
        raise FileExistsError("在线引导包输出已存在，拒绝覆盖。")
    package_root.mkdir(parents=True)

    offline_zip_name = f"Kanyikan-v{version}-windows-amd64-offline.zip"
    release_base_url = f"{repository_url}/releases/download/v{version}"
    script = ONLINE_SCRIPT
    replacements = {
        "__KANYIKAN_VERSION__": version,
        "__KANYIKAN_OFFLINE_ZIP_NAME__": offline_zip_name,
        "__KANYIKAN_PUBLIC_KEY_SHA256__": public_key_sha256,
        "__KANYIKAN_RELEASE_BASE_URL__": release_base_url,
    }
    for placeholder, value in replacements.items():
        if script.count(placeholder) != 1:
            raise RuntimeError(f"在线引导脚本占位符数量不合法：{placeholder}")
        script = script.replace(placeholder, value)

    (package_root / "VERSION").write_text(version + "\n", encoding="utf-8", newline="\n")
    (package_root / "install-online.cmd").write_text(
        '@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\install-online.ps1" %*\r\nexit /b %ERRORLEVEL%\r\n',
        encoding="utf-8",
        newline="",
    )
    (package_root / "install-online.ps1").write_text(script, encoding="utf-8-sig", newline="\r\n")
    (package_root / "public-key.pem").write_bytes(public_key)
    _write_zip(package_root, online_zip, published)

    return {
        "packageRoot": str(package_root),
        "onlineZip": str(online_zip),
        "onlineZipSha256": _sha256(online_zip),
        "publicKeySha256": public_key_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--repository-url", required=True)
    args = parser.parse_args()
    result = build_online_release(
        output_directory=args.output_directory,
        version=args.version,
        published_at=args.published_at,
        public_key_path=args.public_key,
        repository_url=args.repository_url,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
