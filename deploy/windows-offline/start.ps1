$ErrorActionPreference = "Stop"
# Keep this script ASCII-only for Windows PowerShell 5.1 compatibility.
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = Join-Path $Root "app"
$PythonExe = Join-Path $Root "runtime\python\python.exe"
$DataRoot = Join-Path $AppRoot "data"
$PidFile = Join-Path $DataRoot "puricore-windows.pid"
$OutLog = Join-Path $DataRoot "puricore-windows.out.log"
$ErrorLog = Join-Path $DataRoot "puricore-windows.error.log"
$LocalUrl = "http://127.0.0.1:7800"

function Open-Puricore {
    Start-Process "$LocalUrl/"
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -ExpandProperty IPAddress -Unique
    Write-Host "PC: $LocalUrl"
    foreach ($address in $addresses) {
        Write-Host "Tablet/mobile: http://${address}:7800"
    }
}

try {
    if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python runtime is missing. Run the USB installer again." }
    if (-not (Test-Path -LiteralPath $AppRoot)) { throw "Application files are missing. Run the USB installer again." }
    New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null

    if (Test-Path -LiteralPath $PidFile) {
        $savedPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
        if ($savedPid -match '^\d+$') {
            $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
            if ($null -ne $existing -and $existing.CommandLine -match "lab_device_manager") {
                Write-Host "Puricore is already running."
                Open-Puricore
                exit 0
            }
        }
        Remove-Item -LiteralPath $PidFile -Force
    }

    $listener = Get-NetTCPConnection -LocalPort 7800 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $listener) { throw "Port 7800 is already used by another program (PID $($listener.OwningProcess))." }

    $process = Start-Process -FilePath $PythonExe -ArgumentList @("-m", "lab_device_manager") `
        -WorkingDirectory $AppRoot -RedirectStandardOutput $OutLog -RedirectStandardError $ErrorLog -PassThru
    Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII

    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Seconds 1
        if ($process.HasExited) {
            throw "Puricore exited during startup. Check $ErrorLog"
        }
        try {
            $response = Invoke-WebRequest -Uri "$LocalUrl/login" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                Write-Host "Puricore started successfully."
                Open-Puricore
                exit 0
            }
        } catch { }
    }
    throw "Startup timed out after 120 seconds. Check $ErrorLog"
} catch {
    Write-Error $_
    exit 1
}
