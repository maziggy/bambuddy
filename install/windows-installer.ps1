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
    - Stores user data and application logs outside the Git checkout
    - Creates Python venv
    - Installs requirements
    - Lets user choose port, default 8000
    - Creates installer log
    - Creates runtime log
    - Optionally creates Windows Firewall rule
    - Creates Start-Bambuddy.ps1
    - Optionally registers Bambuddy as Windows Service using NSSM
    - Optionally starts Bambuddy

.PARAMETER Yes
    Runs unattended and accepts defaults for prompts.

.PARAMETER Silent
    Runs unattended with reduced console output.
#>

[CmdletBinding()]
param (
    [ValidateNotNullOrEmpty()]
    [string]$InstallDir = "C:\Bambuddy",

    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [switch]$Yes,
    [switch]$Silent,
    [switch]$NoService,
    [switch]$NoStart,
    [switch]$LocalOnly
)

$ErrorActionPreference = "Stop"

$script:LogFile = $null
$script:Yes = [bool]$Yes
$script:Silent = [bool]$Silent

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

    if (-not $script:Silent) {
        Write-Host $Message -ForegroundColor $Color
    }

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

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Restart-AsAdmin {
    Write-Host "Script is not running as Administrator. Relaunching elevated..." -ForegroundColor Yellow

    try {
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")

        if ($InstallDir -ne "C:\Bambuddy") {
            $arguments += @("-InstallDir", "`"$InstallDir`"")
        }

        if ($Port -ne 8000) {
            $arguments += @("-Port", $Port)
        }

        if ($Yes) { $arguments += "-Yes" }
        if ($Silent) { $arguments += "-Silent" }
        if ($NoService) { $arguments += "-NoService" }
        if ($NoStart) { $arguments += "-NoStart" }
        if ($LocalOnly) { $arguments += "-LocalOnly" }

        Start-Process powershell.exe `
            -ArgumentList $arguments `
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

    if ($script:Yes -or $script:Silent) {
        return $DefaultYes
    }

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
            "n"     { return $false }
            "no"    { return $false }
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

    if ($script:Yes -or $script:Silent) {
        return $DefaultPort
    }

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

function Set-FolderPermissions {
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
        if (Test-PythonVersion -PythonCommand "python") {
            return "python"
        }
    }

    if (Test-CommandExists "py") {
        if (Test-PythonVersion -PythonCommand "py") {
            return "py"
        }
    }

    return $null
}

function Test-PythonVersion {
    param (
        [Parameter(Mandatory = $true)]
        [string]$PythonCommand
    )

    try {
        if ($PythonCommand -eq "py") {
            $versionOutput = & py -3 --version 2>&1
        }
        else {
            $versionOutput = & python --version 2>&1
        }

        if ($versionOutput -match "Python\s+(\d+)\.(\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]

            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
                return $true
            }

            Write-Log "Found $versionOutput, but Bambuddy requires Python 3.10 or newer." "WARN" Yellow
        }
    }
    catch {}

    return $false
}

function Test-PortAvailable {
    param (
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    try {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return -not $listener
    }
    catch {
        Write-Log "Could not check whether TCP port $Port is already in use. Continuing." "WARN" Yellow
        return $true
    }
}

function Move-LegacyRuntimeData {
    param (
        [Parameter(Mandatory = $true)]
        [string]$BambuddyDir,

        [Parameter(Mandatory = $true)]
        [string]$DataDir,

        [Parameter(Mandatory = $true)]
        [string]$LogDir
    )

    $legacyMappings = @(
        @{ Source = (Join-Path $BambuddyDir "bambuddy.db"); Destination = (Join-Path $DataDir "bambuddy.db") },
        @{ Source = (Join-Path $BambuddyDir "archive"); Destination = (Join-Path $DataDir "archive") },
        @{ Source = (Join-Path $BambuddyDir "data"); Destination = (Join-Path $DataDir "data") }
    )

    foreach ($mapping in $legacyMappings) {
        if ((Test-Path $mapping.Source) -and (-not (Test-Path $mapping.Destination))) {
            Write-Log "Moving legacy runtime data from '$($mapping.Source)' to '$($mapping.Destination)'." "INFO" Cyan
            Move-Item -Path $mapping.Source -Destination $mapping.Destination -Force
        }
        elseif ((Test-Path $mapping.Source) -and (Test-Path $mapping.Destination)) {
            Write-Log "Legacy runtime data remains at '$($mapping.Source)' because '$($mapping.Destination)' already exists." "WARN" Yellow
        }
    }

    $legacyLogDir = Join-Path $BambuddyDir "logs"
    if (Test-Path $legacyLogDir) {
        $legacyLogs = Get-ChildItem -Path $legacyLogDir -Force -ErrorAction SilentlyContinue

        foreach ($legacyLog in $legacyLogs) {
            $destination = Join-Path $LogDir $legacyLog.Name

            if (-not (Test-Path $destination)) {
                Write-Log "Moving legacy log '$($legacyLog.FullName)' to '$destination'." "INFO" Cyan
                Move-Item -Path $legacyLog.FullName -Destination $destination -Force
            }
        }
    }
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
    $expectedNssmSha256 = "727D1E42275C605E0F04ABA98095C38A8E1E46DEF453CDFFCE42869428AA6743"

    Write-Log "Downloading NSSM from $nssmUrl" "INFO" Cyan

    Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing

    $actualNssmSha256 = (Get-FileHash -Path $nssmZip -Algorithm SHA256).Hash
    if ($actualNssmSha256 -ne $expectedNssmSha256) {
        Remove-Item $nssmZip -Force -ErrorAction SilentlyContinue
        throw "NSSM download checksum mismatch. Expected $expectedNssmSha256 but got $actualNssmSha256."
    }

    Write-Log "NSSM checksum verified." "INFO" Green

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
        [string]$RuntimeLogPath,

        [Parameter(Mandatory = $true)]
        [string]$RuntimeErrorLogPath,

        [Parameter(Mandatory = $true)]
        [string]$DataDir,

        [Parameter(Mandatory = $true)]
        [string]$LogDir
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
    & $nssmExe set $ServiceName AppEnvironmentExtra "+DATA_DIR=$DataDir" "+LOG_DIR=$LogDir"

    # Logging
    & $nssmExe set $ServiceName AppStdout $RuntimeLogPath
    & $nssmExe set $ServiceName AppStderr $RuntimeErrorLogPath
    & $nssmExe set $ServiceName AppRotateFiles 1
    & $nssmExe set $ServiceName AppRotateOnline 1
    & $nssmExe set $ServiceName AppRotateSeconds 86400
    & $nssmExe set $ServiceName AppRotateBytes 10485760

    # Restart behavior
    & $nssmExe set $ServiceName AppExit Default Exit
    & $nssmExe set $ServiceName AppExit 0 Exit
    & $nssmExe set $ServiceName AppExit 1 Restart
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
        Restart-AsAdmin
    }

    Write-Host ""
    Write-Host "=== Bambuddy Windows Installer ===" -ForegroundColor Green
    Write-Host ""

    # ------------------------------------------------------------
    # Install directory
    # ------------------------------------------------------------

    $defaultInstallDir = $InstallDir

    if ($PSBoundParameters.ContainsKey("InstallDir")) {
        $installDir = $InstallDir.Trim('"')
    }
    else {
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
    }

    if (-not (Test-Path $installDir)) {
        New-Item -Path $installDir -ItemType Directory -Force | Out-Null
    }

    Start-InstallerLogging -InstallDir $installDir

    Write-Log "Install directory: $installDir" "INFO" Cyan

    Set-FolderPermissions -Path $installDir

    # ------------------------------------------------------------
    # Port selection
    # ------------------------------------------------------------

    $port = Read-Port -DefaultPort $Port

    if (-not (Test-PortAvailable -Port $port)) {
        throw "TCP port $port is already in use. Choose another port with -Port or stop the conflicting service."
    }

    Write-Log "Selected port: $port" "INFO" Cyan

    $exposeOnLan = -not $LocalOnly
    if (-not $LocalOnly) {
        $exposeOnLan = Read-YesNo -Question "Expose Bambuddy on the LAN? Choose No to bind only to this computer." -DefaultYes $true
    }

    if ($exposeOnLan) {
        $bindAddress = "0.0.0.0"
    }
    else {
        $bindAddress = "127.0.0.1"
    }

    Write-Log "Bind address: $bindAddress" "INFO" Cyan

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
        throw "Python 3.10 or newer was not found in PATH. Restart PowerShell after installation or install Python 3.10+ manually."
    }

    Write-Log "Python command: $pythonCommand" "INFO" Green

    # ------------------------------------------------------------
    # Clone or update Bambuddy repository
    # ------------------------------------------------------------

    Write-Log "Preparing Bambuddy repository..." "INFO" Cyan

    $bambuddyRepoUrl = "https://github.com/maziggy/bambuddy.git"
    $bambuddyFolderName = "bambuddy"
    $bambuddyDir = Join-Path $installDir $bambuddyFolderName
    $dataDir = Join-Path $installDir "data"
    $appLogDir = Join-Path $installDir "logs"

    Write-Log "Repository target: $bambuddyDir" "INFO" Cyan

    Set-FolderPermissions -Path $installDir
    Set-FolderPermissions -Path $dataDir
    Set-FolderPermissions -Path $appLogDir

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

            $removeBroken = Read-YesNo -Question "Remove this directory and clone again? This deletes '$bambuddyDir'." -DefaultYes $false

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

        & git clone --depth=1 --progress $bambuddyRepoUrl $bambuddyFolderName

        $gitCloneExitCode = $LASTEXITCODE

        Pop-Location

        if ($gitCloneExitCode -ne 0) {
            throw "Failed to clone Bambuddy repository to '$bambuddyDir'."
        }
    }

    if (-not (Test-Path $bambuddyDir)) {
        throw "Bambuddy directory was not created: $bambuddyDir"
    }

    Move-LegacyRuntimeData -BambuddyDir $bambuddyDir -DataDir $dataDir -LogDir $appLogDir

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
        "`$BindAddress = `"$bindAddress`"",
        "`$env:DATA_DIR = `"$dataDir`"",
        "`$env:LOG_DIR = `"$appLogDir`"",
        '',
        'Set-Location "$BambuddyDir"',
        '',
        'Write-Output "Starting Bambuddy on port $Port"',
        'Write-Output "Bind address: $BindAddress"',
        'Write-Output "Working directory: $BambuddyDir"',
        'Write-Output "Python executable: $VenvPython"',
        'Write-Output "Data directory: $env:DATA_DIR"',
        'Write-Output "Log directory: $env:LOG_DIR"',
        '',
        '& "$VenvPython" -m uvicorn backend.app.main:app --host $BindAddress --port $Port'
    )

    $startScriptContent = $startScriptLines -join [Environment]::NewLine

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($startScriptPath, $startScriptContent, $utf8NoBom)

    Write-Log "Start script created: $startScriptPath" "INFO" Green
    Write-Log "Runtime log path: $runtimeLogPath" "INFO" Green
    Write-Log "Runtime error log path: $runtimeErrorLogPath" "INFO" Green

    # ------------------------------------------------------------
    # Optional Windows Service registration
    # ------------------------------------------------------------

    $registerService = (-not $NoService) -and (Read-YesNo -Question "Register Bambuddy as a Windows Service?" -DefaultYes $true)

    if ($registerService) {
        Register-BambuddyService `
            -ServiceName "Bambuddy" `
            -StartScriptPath $startScriptPath `
            -InstallDir $installDir `
            -BambuddyDir $bambuddyDir `
            -RuntimeLogPath $runtimeLogPath `
            -RuntimeErrorLogPath $runtimeErrorLogPath `
            -DataDir $dataDir `
            -LogDir $appLogDir
    }

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    Write-Host ""
    Write-Host "=== Installation completed ===" -ForegroundColor Green
    Write-Host "Install directory: $installDir"
    Write-Host "Repository path:   $bambuddyDir"
    Write-Host "Data directory:    $dataDir"
    Write-Host "App log directory: $appLogDir"
    Write-Host "Port:              $port"
    Write-Host "Bind address:      $bindAddress"
    Write-Host "Installer log:     $script:LogFile"
    Write-Host "Service stdout:    $runtimeLogPath"
    Write-Host "Service stderr:    $runtimeErrorLogPath"
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

    if ((-not $registerService) -and (-not $NoStart) -and (-not $script:Yes) -and (-not $script:Silent)) {
        $startNow = Read-YesNo -Question "Start Bambuddy now?" -DefaultYes $true

        if ($startNow) {
            Write-Log "Starting Bambuddy manually..." "INFO" Green
            Write-Host "Local URL:   http://localhost:$port"
            if ($bindAddress -eq "0.0.0.0") {
                Write-Host "Network URL: http://<this-computer-ip>:$port"
            }
            Write-Host "Press CTRL+C to stop Bambuddy."
            Write-Host ""

            Set-Location $bambuddyDir
            $env:DATA_DIR = $dataDir
            $env:LOG_DIR = $appLogDir
            & $venvPython -m uvicorn backend.app.main:app --host $bindAddress --port $port
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
