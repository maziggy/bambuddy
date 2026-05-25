#requires -version 5.1

<#
.SYNOPSIS
    Bambuddy Windows Installer

.DESCRIPTION
    - Uses default install directory C:\Bambuddy
    - Lets user choose custom install directory
    - Checks and installs Git if missing
    - Checks and installs Python 3 if missing
    - Fixes permissions on install directory
    - Clones Bambuddy repository
    - Creates Python venv
    - Installs requirements
    - Lets user choose port, default 8000
    - Creates installer log
    - Creates runtime log
    - Optionally creates Windows Firewall rule
    - Creates Start-Bambuddy.ps1
    - Optionally registers Bambuddy as Windows Service using NSSM
    - Optionally starts Bambuddy
#>

$ErrorActionPreference = "Stop"

$script:LogFile = $null
$script:TranscriptStarted = $false

# ------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------

function Write-Log {
    param (
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [string]$Level = "INFO",

        [ConsoleColor]$Color = [ConsoleColor]::White
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] [$Level] $Message"

    Write-Host $Message -ForegroundColor $Color

    if ($script:LogFile) {
        try {
            $line | Out-File -FilePath $script:LogFile -Append -Encoding UTF8
        }
        catch {
            Write-Host "Could not write to log file: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

function Start-InstallerLogging {
    param (
        [Parameter(Mandatory = $true)]
        [string]$InstallDir
    )

    $script:LogFile = Join-Path $InstallDir "install.log"

    if (-not (Test-Path $InstallDir)) {
        New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
    }

    try {
        "" | Out-File -FilePath $script:LogFile -Append -Encoding UTF8
        "============================================================" | Out-File -FilePath $script:LogFile -Append -Encoding UTF8
        "Bambuddy Installer started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $script:LogFile -Append -Encoding UTF8
        "============================================================" | Out-File -FilePath $script:LogFile -Append -Encoding UTF8

        Write-Log "Logging enabled: $script:LogFile" "INFO" Green
    }
    catch {
        Write-Host "Could not initialize installer log: $($_.Exception.Message)" -ForegroundColor Yellow
        $script:LogFile = $null
    }
}

function Stop-InstallerLogging {
    # No transcript is used anymore.
}

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Relaunch-AsAdmin {
    Write-Host "Script is not running as Administrator. Relaunching elevated..." -ForegroundColor Yellow

    try {
        Start-Process powershell.exe `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" `
            -Verb RunAs

        exit
    }
    catch {
        Write-Host "Elevation cancelled or failed. Please run PowerShell as Administrator." -ForegroundColor Red
        exit 1
    }
}

function Test-CommandExists {
    param (
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Install-WithWinget {
    param (
        [Parameter(Mandatory = $true)]
        [string]$PackageId,

        [Parameter(Mandatory = $true)]
        [string]$DisplayName
    )

    if (-not (Test-CommandExists "winget")) {
        throw "winget is not available. Please install $DisplayName manually and run this script again."
    }

    Write-Log "Installing $DisplayName via winget..." "INFO" Cyan

    & winget install `
        --id $PackageId `
        --exact `
        --silent `
        --accept-package-agreements `
        --accept-source-agreements

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install $DisplayName via winget."
    }

    Refresh-Path
}

function Read-YesNo {
    param (
        [Parameter(Mandatory = $true)]
        [string]$Question,

        [bool]$DefaultYes = $true
    )

    if ($DefaultYes) {
        $suffix = "[Y/n]"
    }
    else {
        $suffix = "[y/N]"
    }

    while ($true) {
        $answer = Read-Host "$Question $suffix"

        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $DefaultYes
        }

        switch ($answer.ToLower()) {
            "y"     { return $true }
            "yes"   { return $true }
            "j"     { return $true }
            "ja"    { return $true }
            "n"     { return $false }
            "no"    { return $false }
            "nein"  { return $false }
            default {
                Write-Host "Please answer yes or no." -ForegroundColor Yellow
            }
        }
    }
}

function Read-Port {
    param (
        [int]$DefaultPort = 8000
    )

    while ($true) {
        $inputPort = Read-Host "Enter Bambuddy port or press Enter for default [$DefaultPort]"

        if ([string]::IsNullOrWhiteSpace($inputPort)) {
            return $DefaultPort
        }

        $parsedPort = 0

        if ([int]::TryParse($inputPort, [ref]$parsedPort)) {
            if ($parsedPort -ge 1 -and $parsedPort -le 65535) {
                return $parsedPort
            }
        }

        Write-Host "Invalid port. Please enter a number between 1 and 65535." -ForegroundColor Yellow
    }
}

function Test-WriteAccess {
    param (
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    try {
        if (-not (Test-Path $Path)) {
            New-Item -Path $Path -ItemType Directory -Force | Out-Null
        }

        $testFile = Join-Path $Path "write-test.tmp"

        "test" | Set-Content -Path $testFile -Force
        Remove-Item $testFile -Force

        return $true
    }
    catch {
        return $false
    }
}

function Fix-FolderPermissions {
    param (
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Write-Log "Fixing permissions for: $Path" "INFO" Cyan

    if (-not (Test-Path $Path)) {
        New-Item -Path $Path -ItemType Directory -Force | Out-Null
    }

    $currentUser = "$env:USERDOMAIN\$env:USERNAME"

    try {
        # Enable inheritance
        & icacls $Path /inheritance:e | Out-Null

        # Grant local Administrators full access by SID, language independent
        & icacls $Path /grant "*S-1-5-32-544:(OI)(CI)F" /T /C | Out-Null

        # Grant current user full access
        & icacls $Path /grant "$($currentUser):(OI)(CI)F" /T /C | Out-Null
    }
    catch {
        Write-Log "Permission adjustment failed or partially failed. Continuing with write test..." "WARN" Yellow
    }

    if (-not (Test-WriteAccess -Path $Path)) {
        throw "No write permission to '$Path'. Try another path, for example C:\Temp\Bambuddy, or check Windows Defender Controlled Folder Access."
    }

    Write-Log "Write access confirmed." "INFO" Green
}

function Get-PythonCommand {
    if (Test-CommandExists "python") {
        try {
            $version = & python --version 2>&1

            if ($version -match "Python 3") {
                return "python"
            }
        }
        catch {}
    }

    if (Test-CommandExists "py") {
        try {
            $version = & py -3 --version 2>&1

            if ($version -match "Python 3") {
                return "py"
            }
        }
        catch {}
    }

    return $null
}

function Invoke-Python {
    param (
        [Parameter(Mandatory = $true)]
        [string]$PythonCommand,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($PythonCommand -eq "py") {
        & py -3 @Arguments
    }
    else {
        & python @Arguments
    }

    return $LASTEXITCODE
}

function Install-NSSM {
    param (
        [Parameter(Mandatory = $true)]
        [string]$InstallDir
    )

    $nssmDir = Join-Path $InstallDir "nssm"
    $nssmExe = Join-Path $nssmDir "nssm.exe"

    if (Test-Path $nssmExe) {
        Write-Log "NSSM already exists: $nssmExe" "INFO" Green
        return $nssmExe
    }

    Write-Log "Installing NSSM..." "INFO" Cyan

    $nssmZip = Join-Path $InstallDir "nssm.zip"
    $nssmExtract = Join-Path $InstallDir "nssm_extract"

    if (Test-Path $nssmExtract) {
        Remove-Item $nssmExtract -Recurse -Force
    }

    New-Item -Path $nssmDir -ItemType Directory -Force | Out-Null

    $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"

    Write-Log "Downloading NSSM from $nssmUrl" "INFO" Cyan

    Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing

    Expand-Archive -Path $nssmZip -DestinationPath $nssmExtract -Force

    $possibleNssmExe = Get-ChildItem -Path $nssmExtract -Recurse -Filter "nssm.exe" |
        Where-Object { $_.FullName -match "\\win64\\" } |
        Select-Object -First 1

    if (-not $possibleNssmExe) {
        throw "Could not find NSSM win64 executable after extraction."
    }

    Copy-Item -Path $possibleNssmExe.FullName -Destination $nssmExe -Force

    Remove-Item $nssmZip -Force -ErrorAction SilentlyContinue
    Remove-Item $nssmExtract -Recurse -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $nssmExe)) {
        throw "NSSM installation failed. nssm.exe was not found."
    }

    Write-Log "NSSM installed: $nssmExe" "INFO" Green

    return $nssmExe
}

function Register-BambuddyService {
    param (
        [Parameter(Mandatory = $true)]
        [string]$ServiceName,

        [Parameter(Mandatory = $true)]
        [string]$StartScriptPath,

        [Parameter(Mandatory = $true)]
        [string]$InstallDir,

        [Parameter(Mandatory = $true)]
        [string]$BambuddyDir,

        [Parameter(Mandatory = $true)]
        [string]$RuntimeLogPath
    )

    Write-Log "Preparing Windows Service registration using NSSM..." "INFO" Cyan

    $nssmExe = Install-NSSM -InstallDir $InstallDir

    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

    if ($existingService) {
        Write-Log "Service '$ServiceName' already exists." "WARN" Yellow

        $replaceService = Read-YesNo -Question "Do you want to replace the existing service '$ServiceName'?" -DefaultYes $true

        if ($replaceService) {
            Write-Log "Stopping existing service if running..." "INFO" Cyan

            try {
                Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
            }
            catch {}

            Write-Log "Removing existing service..." "INFO" Cyan

            & $nssmExe remove $ServiceName confirm | Out-Null

            Start-Sleep -Seconds 2
        }
        else {
            Write-Log "Keeping existing service. Service registration skipped." "WARN" Yellow
            return
        }
    }

    $powerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $serviceArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScriptPath`""

    Write-Log "Creating NSSM service '$ServiceName'..." "INFO" Cyan

    & $nssmExe install $ServiceName $powerShellExe $serviceArguments

    if ($LASTEXITCODE -ne 0) {
        throw "NSSM failed to create service '$ServiceName'."
    }

    & $nssmExe set $ServiceName DisplayName "Bambuddy"
    & $nssmExe set $ServiceName Description "Bambuddy backend service"
    & $nssmExe set $ServiceName AppDirectory $BambuddyDir
    & $nssmExe set $ServiceName Start SERVICE_AUTO_START

    # Logging
    & $nssmExe set $ServiceName AppStdout $RuntimeLogPath
    & $nssmExe set $ServiceName AppStderr $RuntimeLogPath
    & $nssmExe set $ServiceName AppRotateFiles 1
    & $nssmExe set $ServiceName AppRotateOnline 1
    & $nssmExe set $ServiceName AppRotateSeconds 86400
    & $nssmExe set $ServiceName AppRotateBytes 10485760

    # Restart behavior
    & $nssmExe set $ServiceName AppExit Default Restart
    & $nssmExe set $ServiceName AppRestartDelay 5000

    Write-Log "Service '$ServiceName' created successfully with NSSM." "INFO" Green

    $startServiceNow = Read-YesNo -Question "Start Windows Service '$ServiceName' now?" -DefaultYes $true

    if ($startServiceNow) {
        Write-Log "Starting service '$ServiceName'..." "INFO" Cyan

        Start-Service -Name $ServiceName

        Start-Sleep -Seconds 5

        $service = Get-Service -Name $ServiceName

        Write-Log "Service state: $($service.Status)" "INFO" Green

        if ($service.Status -ne "Running") {
            Write-Log "Service did not stay running. Check runtime log: $RuntimeLogPath" "WARN" Yellow
        }
    }
}

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

try {
    if (-not (Test-IsAdmin)) {
        Relaunch-AsAdmin
    }

    Write-Host ""
    Write-Host "=== Bambuddy Windows Installer ===" -ForegroundColor Green
    Write-Host ""

    # ------------------------------------------------------------
    # Set Execution Policy
    # ------------------------------------------------------------

    Set-ExecutionPolicy -ExecutionPolicy AllSigned -Scope Process -Force
    Write-Log "Execution policy set to AllSigned for this process." "INFO" Green

    # ------------------------------------------------------------
    # Install directory
    # ------------------------------------------------------------

    $defaultInstallDir = "C:\Bambuddy"

    $useDefaultDir = Read-YesNo -Question "Use default install directory '$defaultInstallDir'?" -DefaultYes $true

    if ($useDefaultDir) {
        $installDir = $defaultInstallDir
    }
    else {
        while ($true) {
            $customDir = Read-Host "Enter custom install directory"

            if (-not [string]::IsNullOrWhiteSpace($customDir)) {
                $installDir = $customDir.Trim('"')
                break
            }

            Write-Host "Install directory cannot be empty." -ForegroundColor Yellow
        }
    }

    if (-not (Test-Path $installDir)) {
        New-Item -Path $installDir -ItemType Directory -Force | Out-Null
    }

    Start-InstallerLogging -InstallDir $installDir

    Write-Log "Install directory: $installDir" "INFO" Cyan

    Fix-FolderPermissions -Path $installDir

    # ------------------------------------------------------------
    # Port selection
    # ------------------------------------------------------------

    $port = Read-Port -DefaultPort 8000
    Write-Log "Selected port: $port" "INFO" Cyan

    # ------------------------------------------------------------
    # Git check / install
    # ------------------------------------------------------------

    Write-Log "Checking Git..." "INFO" Cyan

    if (-not (Test-CommandExists "git")) {
        Write-Log "Git is not installed." "WARN" Yellow
        Install-WithWinget -PackageId "Git.Git" -DisplayName "Git"
        Refresh-Path
    }

    if (-not (Test-CommandExists "git")) {
        throw "Git was installed, but is still not available in PATH. Restart PowerShell and run this script again."
    }

    $gitVersion = & git --version
    Write-Log "Git found: $gitVersion" "INFO" Green

    # ------------------------------------------------------------
    # Python check / install
    # ------------------------------------------------------------

    Write-Log "Checking Python..." "INFO" Cyan

    $pythonCommand = Get-PythonCommand

    if (-not $pythonCommand) {
        Write-Log "Python 3 is not installed." "WARN" Yellow
        Install-WithWinget -PackageId "Python.Python.3.12" -DisplayName "Python 3"
        Refresh-Path
        $pythonCommand = Get-PythonCommand
    }

    if (-not $pythonCommand) {
        throw "Python was installed, but is still not available in PATH. Restart PowerShell and run this script again."
    }

    Write-Log "Python command: $pythonCommand" "INFO" Green

    # ------------------------------------------------------------
    # Clone or update Bambuddy repository
    # ------------------------------------------------------------

    Write-Log "Preparing Bambuddy repository..." "INFO" Cyan

    $bambuddyRepoUrl = "https://github.com/maziggy/bambuddy.git"
    $bambuddyFolderName = "bambuddy"
    $bambuddyDir = Join-Path $installDir $bambuddyFolderName

    Write-Log "Repository target: $bambuddyDir" "INFO" Cyan

    Fix-FolderPermissions -Path $installDir

    if (Test-Path $bambuddyDir) {
        $gitDir = Join-Path $bambuddyDir ".git"

        if (Test-Path $gitDir) {
            Write-Log "Existing Bambuddy Git repository found." "WARN" Yellow

            $updateExisting = Read-YesNo -Question "Do you want to update the existing repository with git pull?" -DefaultYes $true

            if ($updateExisting) {
                Push-Location $bambuddyDir

                Write-Log "Running git pull..." "INFO" Cyan
                & git pull

                $gitPullExitCode = $LASTEXITCODE
                Pop-Location

                if ($gitPullExitCode -ne 0) {
                    throw "git pull failed."
                }
            }
        }
        else {
            Write-Log "Target directory exists but is not a valid Git repository: $bambuddyDir" "WARN" Yellow

            $removeBroken = Read-YesNo -Question "Remove this directory and clone again?" -DefaultYes $true

            if ($removeBroken) {
                Write-Log "Removing existing target directory..." "INFO" Cyan
                Remove-Item $bambuddyDir -Recurse -Force
            }
            else {
                throw "Cannot continue because '$bambuddyDir' already exists and is not a Git repository."
            }
        }
    }

    if (-not (Test-Path $bambuddyDir)) {
        Write-Log "Testing folder creation before git clone..." "INFO" Cyan

        $testCloneDir = Join-Path $installDir "git-write-test"

        if (Test-Path $testCloneDir) {
            Remove-Item $testCloneDir -Recurse -Force
        }

        New-Item -Path $testCloneDir -ItemType Directory -Force | Out-Null
        "test" | Set-Content -Path (Join-Path $testCloneDir "test.txt") -Force
        Remove-Item $testCloneDir -Recurse -Force

        Write-Log "Folder creation test successful." "INFO" Green

        Write-Log "Cloning Bambuddy repository..." "INFO" Cyan

        Push-Location $installDir

        & git clone --progress $bambuddyRepoUrl $bambuddyFolderName

        $gitCloneExitCode = $LASTEXITCODE

        Pop-Location

        if ($gitCloneExitCode -ne 0) {
            throw "Failed to clone Bambuddy repository to '$bambuddyDir'."
        }
    }

    if (-not (Test-Path $bambuddyDir)) {
        throw "Bambuddy directory was not created: $bambuddyDir"
    }

    # ------------------------------------------------------------
    # Python virtual environment
    # ------------------------------------------------------------

    Write-Log "Setting up Python virtual environment..." "INFO" Cyan

    Push-Location $bambuddyDir

    $venvDir = Join-Path $bambuddyDir "venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    $venvPip = Join-Path $venvDir "Scripts\pip.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Log "Creating virtual environment..." "INFO" Cyan

        $venvExitCode = Invoke-Python -PythonCommand $pythonCommand -Arguments @("-m", "venv", "venv")

        if ($venvExitCode -ne 0) {
            Pop-Location
            throw "Failed to create Python virtual environment."
        }
    }
    else {
        Write-Log "Virtual environment already exists." "INFO" Green
    }

    if (-not (Test-Path $venvPython)) {
        Pop-Location
        throw "Virtual environment Python executable was not found: $venvPython"
    }

    # ------------------------------------------------------------
    # Install requirements
    # ------------------------------------------------------------

    Write-Log "Installing Python dependencies..." "INFO" Cyan

    $requirementsFile = Join-Path $bambuddyDir "requirements.txt"

    if (-not (Test-Path $requirementsFile)) {
        Pop-Location
        throw "requirements.txt was not found in $bambuddyDir"
    }

    Write-Log "Upgrading pip..." "INFO" Cyan
    & $venvPython -m pip install --upgrade pip

    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        throw "Failed to upgrade pip."
    }

    Write-Log "Installing requirements.txt..." "INFO" Cyan
    & $venvPip install -r $requirementsFile

    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        throw "Failed to install Python requirements."
    }

    Pop-Location

    # ------------------------------------------------------------
    # Firewall rule
    # ------------------------------------------------------------

    $createFirewallRule = Read-YesNo -Question "Create Windows Firewall rule for TCP port $port?" -DefaultYes $true

    if ($createFirewallRule) {
        $ruleName = "Bambuddy TCP $port"

        $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

        if (-not $existingRule) {
            Write-Log "Creating firewall rule: $ruleName" "INFO" Cyan

            New-NetFirewallRule `
                -DisplayName $ruleName `
                -Direction Inbound `
                -Protocol TCP `
                -LocalPort $port `
                -Action Allow | Out-Null

            Write-Log "Firewall rule created." "INFO" Green
        }
        else {
            Write-Log "Firewall rule already exists: $ruleName" "WARN" Yellow
        }
    }

    # ------------------------------------------------------------
    # Create start script
    # ------------------------------------------------------------

    Write-Log "Creating start script..." "INFO" Cyan

    $startScriptPath = Join-Path $installDir "Start-Bambuddy.ps1"
    $runtimeLogPath = Join-Path $installDir "bambuddy-runtime.log"
    $runtimeErrorLogPath = Join-Path $installDir "bambuddy-runtime-error.log"

    $startScriptLines = @(
        '$ErrorActionPreference = "Stop"',
        '',
        "`$BambuddyDir = `"$bambuddyDir`"",
        "`$VenvPython = `"$venvPython`"",
        "`$Port = $port",
        '',
        'Set-Location "$BambuddyDir"',
        '',
        'Write-Output "Starting Bambuddy on port $Port"',
        'Write-Output "Working directory: $BambuddyDir"',
        'Write-Output "Python executable: $VenvPython"',
        '',
        '& "$VenvPython" -m uvicorn backend.app.main:app --host 0.0.0.0 --port $Port'
    )

    $startScriptContent = $startScriptLines -join [Environment]::NewLine

    Set-Content -Path $startScriptPath -Value $startScriptContent -Encoding UTF8

    Write-Log "Start script created: $startScriptPath" "INFO" Green
    Write-Log "Runtime log path: $runtimeLogPath" "INFO" Green
    Write-Log "Runtime error log path: $runtimeErrorLogPath" "INFO" Green

    # ------------------------------------------------------------
    # Optional Windows Service registration
    # ------------------------------------------------------------

    $registerService = Read-YesNo -Question "Register Bambuddy as a Windows Service?" -DefaultYes $true

    if ($registerService) {
        Register-BambuddyService `
            -ServiceName "Bambuddy" `
            -StartScriptPath $startScriptPath `
            -InstallDir $installDir `
            -BambuddyDir $bambuddyDir `
            -RuntimeLogPath $runtimeLogPath
    }

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    Write-Host ""
    Write-Host "=== Installation completed ===" -ForegroundColor Green
    Write-Host "Install directory: $installDir"
    Write-Host "Repository path:   $bambuddyDir"
    Write-Host "Port:              $port"
    Write-Host "Installer log:     $script:LogFile"
    Write-Host "Runtime log:       $runtimeLogPath"
    Write-Host "Start script:      $startScriptPath"
    Write-Host ""
    Write-Host "Manual start:"
    Write-Host "powershell.exe -ExecutionPolicy Bypass -File `"$startScriptPath`""
    Write-Host ""
    Write-Host "Service commands:"
    Write-Host "Start-Service Bambuddy"
    Write-Host "Stop-Service Bambuddy"
    Write-Host "Restart-Service Bambuddy"
    Write-Host "Get-Service Bambuddy"
    Write-Host ""

    # ------------------------------------------------------------
    # Start manually if service was not registered
    # ------------------------------------------------------------

    if (-not $registerService) {
        $startNow = Read-YesNo -Question "Start Bambuddy now?" -DefaultYes $true

        if ($startNow) {
            Write-Log "Starting Bambuddy manually..." "INFO" Green
            Write-Host "Local URL:   http://localhost:$port"
            Write-Host "Network URL: http://<this-computer-ip>:$port"
            Write-Host "Press CTRL+C to stop Bambuddy."
            Write-Host ""

            Set-Location $bambuddyDir
            & $venvPython -m uvicorn backend.app.main:app --host 0.0.0.0 --port $port
        }
    }
}
catch {
    Write-Host ""
    Write-Host "ERROR:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red

    if ($script:LogFile) {
        Add-Content -Path $script:LogFile -Value "[ERROR] $($_.Exception.Message)" -Encoding UTF8
        Write-Host ""
        Write-Host "Installer log: $script:LogFile" -ForegroundColor Yellow
    }

    exit 1
}
finally {
    Stop-InstallerLogging
}