$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$BundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $BundleRoot "payload"
$InstallRoot = "C:\PuricoreLab"
$RuntimeRoot = Join-Path $InstallRoot "runtime\python"
$PythonExe = Join-Path $RuntimeRoot "python.exe"
$AppRoot = Join-Path $InstallRoot "app"
$NewAppRoot = Join-Path $InstallRoot "app.new"
$PreviousAppRoot = Join-Path $InstallRoot "app.previous"
$BackupRoot = Join-Path $InstallRoot "backups"
$LogPath = Join-Path $BundleRoot "install.log"

function Write-Step([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Test-BundleIntegrity {
    $checksumPath = Join-Path $BundleRoot "SHA256SUMS.txt"
    if (-not (Test-Path -LiteralPath $checksumPath)) {
        throw "SHA256SUMS.txt is missing."
    }
    foreach ($line in Get-Content -LiteralPath $checksumPath) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $parts = $line -split '  ', 2
        if ($parts.Count -ne 2) { throw "Invalid checksum line: $line" }
        $expected = $parts[0].Trim().ToLowerInvariant()
        $relativePath = $parts[1].Replace('/', '\')
        $filePath = Join-Path $BundleRoot $relativePath
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            throw "Bundle file is missing: $relativePath"
        }
        $actual = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "Bundle checksum mismatch: $relativePath"
        }
    }
}

function Stop-PuricoreIfRunning {
    $pidFile = Join-Path $AppRoot "data\puricore-windows.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) { return }
    $savedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($savedPid -notmatch '^\d+$') { return }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
    if ($null -ne $process -and $process.CommandLine -match "lab_device_manager") {
        Write-Step "Stopping the currently running Puricore service."
        Stop-Process -Id ([int]$savedPid) -Force
        Start-Sleep -Seconds 2
    }
}

try {
    Set-Content -LiteralPath $LogPath -Value "Puricore offline installer" -Encoding UTF8
    Write-Step "Verifying the offline bundle."
    Test-BundleIntegrity
    Write-Step "Bundle integrity check passed."
    $pythonInstaller = Get-ChildItem -LiteralPath $PayloadRoot -Filter "python-*-amd64.exe" | Select-Object -First 1
    if ($null -eq $pythonInstaller) {
        throw "Offline payload does not contain the Python x64 installer."
    }
    $sourceArchive = Join-Path $PayloadRoot "app-source.zip"
    $requirements = Join-Path $PayloadRoot "requirements.txt"
    $wheelRoot = Join-Path $PayloadRoot "wheels"
    $defaultConfig = Join-Path $PayloadRoot "config.windows.toml"
    foreach ($required in @($pythonInstaller.FullName, $sourceArchive, $requirements, $wheelRoot, $defaultConfig)) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Offline payload is incomplete: $required"
        }
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    Stop-PuricoreIfRunning

    if (Test-Path -LiteralPath $AppRoot) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backupWork = Join-Path $InstallRoot "backup.$stamp"
        New-Item -ItemType Directory -Path $backupWork -Force | Out-Null
        $backupItemCount = 0
        foreach ($name in @("data", "output", "config.local.toml")) {
            $source = Join-Path $AppRoot $name
            if (Test-Path -LiteralPath $source) {
                Copy-Item -LiteralPath $source -Destination $backupWork -Recurse -Force
                $backupItemCount += 1
            }
        }
        if ($backupItemCount -gt 0) {
            $backupArchive = Join-Path $BackupRoot "puricore-data-$stamp.zip"
            Compress-Archive -Path (Join-Path $backupWork "*") -DestinationPath $backupArchive -Force
            Write-Step "Existing data and configuration backed up to $backupArchive"
        }
        Remove-Item -LiteralPath $backupWork -Recurse -Force
    }

    if (-not (Test-Path -LiteralPath $PythonExe)) {
        Write-Step "Installing bundled Python 3.12 runtime."
        New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
        $arguments = @(
            "/quiet", "InstallAllUsers=1", "TargetDir=$RuntimeRoot",
            "Include_pip=1", "Include_test=0", "Include_doc=0",
            "Include_tcltk=0", "Shortcuts=0", "AssociateFiles=0",
            "PrependPath=0", "Include_launcher=0"
        )
        $result = Start-Process -FilePath $pythonInstaller.FullName -ArgumentList $arguments -Wait -PassThru
        if ($result.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $PythonExe)) {
            throw "Python installation failed with exit code $($result.ExitCode)."
        }
    }

    Write-Step "Installing application dependencies from the USB package (network disabled)."
    & $PythonExe -m pip install --no-index --disable-pip-version-check --find-links $wheelRoot --upgrade -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Offline Python dependency installation failed." }

    if (Test-Path -LiteralPath $NewAppRoot) {
        Remove-Item -LiteralPath $NewAppRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $NewAppRoot -Force | Out-Null
    Expand-Archive -LiteralPath $sourceArchive -DestinationPath $NewAppRoot -Force

    foreach ($name in @("data", "output", "config.local.toml")) {
        $oldPath = Join-Path $AppRoot $name
        if (Test-Path -LiteralPath $oldPath) {
            Copy-Item -LiteralPath $oldPath -Destination $NewAppRoot -Recurse -Force
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $NewAppRoot "config.local.toml"))) {
        Copy-Item -LiteralPath $defaultConfig -Destination (Join-Path $NewAppRoot "config.local.toml")
    }
    New-Item -ItemType Directory -Path (Join-Path $NewAppRoot "data") -Force | Out-Null

    Write-Step "Running an offline application self-check."
    Push-Location $NewAppRoot
    try {
        & $PythonExe -c "from lab_device_manager.config import load_config; from lab_device_manager.web.app import create_app; c=load_config(); print('Puricore self-check OK, port', c.web_port)"
        if ($LASTEXITCODE -ne 0) { throw "Application self-check failed." }
    } finally {
        Pop-Location
    }

    if (Test-Path -LiteralPath $PreviousAppRoot) {
        Remove-Item -LiteralPath $PreviousAppRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $AppRoot) {
        Move-Item -LiteralPath $AppRoot -Destination $PreviousAppRoot
    }
    Move-Item -LiteralPath $NewAppRoot -Destination $AppRoot

    foreach ($launcher in @("start.bat", "stop.bat", "start.ps1", "stop.ps1")) {
        Copy-Item -LiteralPath (Join-Path $BundleRoot $launcher) -Destination $InstallRoot -Force
    }

    & icacls.exe $InstallRoot /grant '*S-1-5-32-545:(OI)(CI)M' /T /C | Out-Null
    $ruleName = "Puricore Lab System TCP 7800"
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 7800 -Profile Private | Out-Null
    }

    $desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
    $shell = New-Object -ComObject WScript.Shell
    foreach ($shortcut in @(
        @{Name="Puricore Start.lnk"; Target=(Join-Path $InstallRoot "start.bat")},
        @{Name="Puricore Stop.lnk"; Target=(Join-Path $InstallRoot "stop.bat")}
    )) {
        $link = $shell.CreateShortcut((Join-Path $desktop $shortcut.Name))
        $link.TargetPath = $shortcut.Target
        $link.WorkingDirectory = $InstallRoot
        $link.Save()
    }

    Write-Step "Installation completed at $InstallRoot"
    Write-Host ""
    Write-Host "Installation succeeded. Use the desktop shortcut to start Puricore."
    exit 0
} catch {
    if (-not (Test-Path -LiteralPath $AppRoot) -and (Test-Path -LiteralPath $PreviousAppRoot)) {
        Move-Item -LiteralPath $PreviousAppRoot -Destination $AppRoot -ErrorAction SilentlyContinue
    }
    Write-Step ("ERROR: " + $_.Exception.Message)
    Write-Error $_
    exit 1
}
