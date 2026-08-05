$ErrorActionPreference = "Stop"
# Keep this script ASCII-only for Windows PowerShell 5.1 compatibility.
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root "app\data\puricore-windows.pid"

try {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Host "Puricore is not running."
        exit 0
    }
    $savedPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($savedPid -notmatch '^\d+$') { throw "The PID file is invalid; no process was stopped." }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item -LiteralPath $PidFile -Force
        Write-Host "Puricore was already stopped."
        exit 0
    }
    if ($process.CommandLine -notmatch "lab_device_manager") {
        throw "PID $savedPid is not Puricore; no process was stopped."
    }
    Stop-Process -Id ([int]$savedPid) -Force
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host "Puricore stopped."
    exit 0
} catch {
    Write-Error $_
    exit 1
}
