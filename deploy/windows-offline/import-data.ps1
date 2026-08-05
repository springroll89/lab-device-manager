$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$BundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TransferArchive = Join-Path $BundleRoot "puricore-data-transfer.zip"
$InstallRoot = "C:\PuricoreLab"
$AppRoot = Join-Path $InstallRoot "app"
$DataRoot = Join-Path $AppRoot "data"
$DatabasePath = Join-Path $DataRoot "lab_device_manager.db"
$PythonExe = Join-Path $InstallRoot "runtime\python\python.exe"
$BackupRoot = Join-Path $InstallRoot "backups"
$TempRoot = Join-Path $env:TEMP ("puricore-import-" + [guid]::NewGuid().ToString("N"))

function Stop-PuricoreIfRunning {
    $stopScript = Join-Path $InstallRoot "stop.ps1"
    if (Test-Path -LiteralPath $stopScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop Puricore." }
    }
}

function Start-Puricore {
    $startScript = Join-Path $InstallRoot "start.ps1"
    if (Test-Path -LiteralPath $startScript) {
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript
        )
    }
}

try {
    foreach ($required in @($TransferArchive, $PythonExe, $AppRoot)) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Required file or installation is missing: $required"
        }
    }
    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    Expand-Archive -LiteralPath $TransferArchive -DestinationPath $TempRoot -Force
    $manifestPath = Join-Path $TempRoot "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        throw "The transfer manifest is missing."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.format_version -ne 1 -or $manifest.database_file -ne "lab_device_manager.db") {
        throw "The transfer bundle format is not supported."
    }
    $sourceDatabase = Join-Path $TempRoot $manifest.database_file
    if (-not (Test-Path -LiteralPath $sourceDatabase)) {
        throw "The transfer database is missing."
    }
    $actualHash = (Get-FileHash -LiteralPath $sourceDatabase -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $manifest.database_sha256.ToLowerInvariant()) {
        throw "The transfer database checksum does not match."
    }
    & $PythonExe -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); r=c.execute('PRAGMA integrity_check').fetchone()[0]; c.close(); print(r); raise SystemExit(0 if r == 'ok' else 1)" $sourceDatabase
    if ($LASTEXITCODE -ne 0) { throw "The transfer database failed its integrity check." }

    Stop-PuricoreIfRunning
    New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    if (Test-Path -LiteralPath $DatabasePath) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        Copy-Item -LiteralPath $DatabasePath -Destination (Join-Path $BackupRoot "database-before-import-$stamp.db") -Force
    }
    foreach ($suffix in @("-wal", "-shm")) {
        $sidecar = $DatabasePath + $suffix
        if (Test-Path -LiteralPath $sidecar) {
            Remove-Item -LiteralPath $sidecar -Force
        }
    }
    Copy-Item -LiteralPath $sourceDatabase -Destination $DatabasePath -Force
    Start-Puricore
    Write-Host ""
    Write-Host "Puricore data import completed successfully."
    Write-Host "Inventory, accounts, experiments, and device history were imported."
    exit 0
} catch {
    Write-Error $_
    exit 1
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
