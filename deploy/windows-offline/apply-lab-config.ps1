$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
# Keep this script ASCII-only for Windows PowerShell 5.1 compatibility.

$BundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceConfig = Join-Path $BundleRoot "config.windows.toml"
$InstallRoot = "C:\PuricoreLab"
$AppRoot = Join-Path $InstallRoot "app"
$TargetConfig = Join-Path $AppRoot "config.local.toml"
$PythonExe = Join-Path $InstallRoot "runtime\python\python.exe"
$BackupRoot = Join-Path $InstallRoot "backups"

try {
    foreach ($required in @($SourceConfig, $AppRoot, $PythonExe)) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Required file or directory is missing: $required"
        }
    }

    $stopScript = Join-Path $InstallRoot "stop.ps1"
    if (Test-Path -LiteralPath $stopScript) {
        $stopResult = Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $stopScript
        ) -Wait -PassThru
        if ($stopResult.ExitCode -ne 0) {
            throw "Could not stop Puricore before updating the configuration."
        }
    }

    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $backupPath = $null
    if (Test-Path -LiteralPath $TargetConfig) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backupPath = Join-Path $BackupRoot "config.local.$stamp.toml"
        Copy-Item -LiteralPath $TargetConfig -Destination $backupPath -Force
    }

    Copy-Item -LiteralPath $SourceConfig -Destination $TargetConfig -Force
    Push-Location $AppRoot
    try {
        & $PythonExe -c "from lab_device_manager.config import load_config; c=load_config('config.local.toml'); assert c.discovery.enabled and len(c.discovery.gateways)==3; print('Discovery configuration OK:', [(g.name, g.host, g.ports) for g in c.discovery.gateways])"
        if ($LASTEXITCODE -ne 0) { throw "The new configuration did not pass validation." }
    } catch {
        if ($null -ne $backupPath) {
            Copy-Item -LiteralPath $backupPath -Destination $TargetConfig -Force
        }
        throw
    } finally {
        Pop-Location
    }

    Write-Host "Lab device discovery configuration updated successfully."
    Write-Host "Gateways: 192.168.1.125, 192.168.1.126, 192.168.1.127"
    Write-Host "Ports 1-4 are scanned; detected instruments appear automatically."
    if ($null -ne $backupPath) { Write-Host "Previous configuration: $backupPath" }

    $startScript = Join-Path $InstallRoot "start.bat"
    if (Test-Path -LiteralPath $startScript) {
        Start-Process -FilePath $startScript
    }
    exit 0
} catch {
    Write-Error $_
    exit 1
}
