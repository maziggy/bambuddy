<#
.SYNOPSIS
    BamBuddy Docker installation script for Windows (Docker Desktop).

.DESCRIPTION
    Mirrors install/docker-install.sh for Windows. Verifies Docker Desktop is
    installed and running, downloads docker-compose.yml, rewrites it for
    Docker Desktop (no host networking), writes a .env, and starts the
    container.

.PARAMETER InstallPath
    Installation directory. Default: $env:USERPROFILE\bambuddy

.PARAMETER Port
    Port to expose. Default: 8000

.PARAMETER TimeZone
    IANA timezone string (e.g. Europe/Berlin). Default: derived from
    Get-TimeZone or UTC.

.PARAMETER Build
    Build the image from source instead of pulling the pre-built image.

.PARAMETER Yes
    Non-interactive mode; accept defaults.

.EXAMPLE
    Interactive install (cmd or PowerShell):
      powershell -ExecutionPolicy Bypass -Command "iwr -useb https://raw.githubusercontent.com/maziggy/bambuddy/main/install/docker-install.ps1 -OutFile docker-install.ps1; .\docker-install.ps1"

.EXAMPLE
    Unattended install:
      .\docker-install.ps1 -InstallPath C:\bambuddy -Port 8080 -TimeZone Europe/Berlin -Yes
#>

[CmdletBinding()]
param(
    [string]$InstallPath,
    [int]$Port,
    [string]$TimeZone,
    [switch]$Build,
    [switch]$Yes,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Helpers --------------------------------------------------------------

function Write-Banner {
    Write-Host ''
    Write-Host '==========================================================' -ForegroundColor Cyan
    Write-Host '   Bambuddy - Docker Installation (Windows)' -ForegroundColor Cyan
    Write-Host '==========================================================' -ForegroundColor Cyan
    Write-Host ''
}

function Info    { param($m) Write-Host "[INFO] $m" -ForegroundColor Blue }
function Ok      { param($m) Write-Host "[OK]   $m" -ForegroundColor Green }
function Warn    { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail    { param($m) Write-Host "[ERR]  $m" -ForegroundColor Red }

function Read-Default {
    param([string]$Prompt, [string]$Default)
    if ($Yes) { return $Default }
    $val = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($val)) { return $Default }
    return $val
}

function Read-YesNo {
    param([string]$Prompt, [string]$Default = 'y')
    if ($Yes) { return ($Default -eq 'y') }
    $hint = if ($Default -eq 'y') { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $val = Read-Host "$Prompt $hint"
        if ([string]::IsNullOrWhiteSpace($val)) { $val = $Default }
        switch -Regex ($val.Trim().ToLower()) {
            '^(y|yes)$' { return $true }
            '^(n|no)$'  { return $false }
            default     { Write-Host 'Please answer yes or no.' }
        }
    }
}

function Show-Help {
    Get-Help $PSCommandPath -Full
    exit 0
}

# --- Detection ------------------------------------------------------------

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail 'Docker is not installed.'
        Write-Host ''
        Write-Host '  Install Docker Desktop for Windows:' -ForegroundColor Yellow
        Write-Host '    https://www.docker.com/products/docker-desktop' -ForegroundColor Cyan
        Write-Host ''
        Write-Host '  After install, launch Docker Desktop and re-run this script.' -ForegroundColor Yellow
        exit 1
    }

    Info 'Docker found, checking daemon...'
    try {
        docker info --format '{{.ServerVersion}}' *> $null
        if ($LASTEXITCODE -ne 0) { throw 'docker info failed' }
    } catch {
        Fail 'Docker daemon is not reachable. Is Docker Desktop running?'
        Write-Host ''
        Write-Host '  Open Docker Desktop, wait for the whale icon to settle,' -ForegroundColor Yellow
        Write-Host '  then re-run this script.' -ForegroundColor Yellow
        exit 1
    }
    Ok 'Docker daemon is running'

    docker compose version *> $null
    if ($LASTEXITCODE -eq 0) {
        $script:DockerCompose = 'docker compose'
        Ok 'Found Docker Compose v2'
    } elseif (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        $script:DockerCompose = 'docker-compose'
        Ok 'Found Docker Compose v1'
    } else {
        Fail 'Docker Compose not found. Install Docker Desktop 4.x+ (ships compose v2).'
        exit 1
    }
}

function Get-DefaultTimeZone {
    if ($TimeZone) { return $TimeZone }
    try {
        $tz = (Get-TimeZone).Id
        # Get-TimeZone returns Windows IDs ("W. Europe Standard Time"). Docker
        # expects IANA. Fall back to UTC if we can't translate.
        $iana = ConvertTo-IanaTimeZone $tz
        if ($iana) { return $iana }
    } catch {}
    return 'UTC'
}

function ConvertTo-IanaTimeZone {
    param([string]$WindowsId)
    # Minimal mapping of common Windows IDs to IANA. Users can override via -TimeZone.
    $map = @{
        'UTC'                         = 'UTC'
        'GMT Standard Time'           = 'Europe/London'
        'W. Europe Standard Time'     = 'Europe/Berlin'
        'Central European Standard Time' = 'Europe/Warsaw'
        'Romance Standard Time'       = 'Europe/Paris'
        'Russian Standard Time'       = 'Europe/Moscow'
        'Eastern Standard Time'       = 'America/New_York'
        'Central Standard Time'       = 'America/Chicago'
        'Mountain Standard Time'      = 'America/Denver'
        'Pacific Standard Time'       = 'America/Los_Angeles'
        'AUS Eastern Standard Time'   = 'Australia/Sydney'
        'Tokyo Standard Time'         = 'Asia/Tokyo'
        'China Standard Time'         = 'Asia/Shanghai'
        'India Standard Time'         = 'Asia/Kolkata'
    }
    return $map[$WindowsId]
}

function Get-LanIp {
    try {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
              Where-Object {
                  $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
                  $_.PrefixOrigin -ne 'WellKnown' -and
                  $_.InterfaceAlias -notmatch '^(vEthernet|Loopback)'
              } | Select-Object -First 1
        if ($ip) { return $ip.IPAddress }
    } catch {}
    return '<your-ip>'
}

# --- Steps ----------------------------------------------------------------

function Initialize-InstallDir {
    Info "Creating installation directory: $InstallPath"
    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    }
    Set-Location $InstallPath
    Ok "Directory ready: $InstallPath"
}

function Get-ComposeFile {
    Info 'Downloading docker-compose.yml...'

    if ($Build) {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Fail 'git is required for --Build but was not found. Install Git for Windows: https://git-scm.com/download/win'
            exit 1
        }
        if (Test-Path '.git') {
            Info 'Existing repository found, updating...'
            git fetch origin
            git reset --hard origin/main
        } else {
            git clone https://github.com/maziggy/bambuddy.git .
        }
    } else {
        Invoke-WebRequest `
            -Uri 'https://raw.githubusercontent.com/maziggy/bambuddy/main/docker-compose.yml' `
            -OutFile 'docker-compose.yml' `
            -UseBasicParsing
    }

    Ok 'docker-compose.yml ready'
}

function Write-EnvFile {
    Info 'Writing .env...'
    $envBody = @"
# BamBuddy Docker Configuration
# Generated by docker-install.ps1 on $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

PORT=$Port
TZ=$TimeZone
"@
    [System.IO.File]::WriteAllText((Join-Path $InstallPath '.env'), $envBody)
    Ok '.env written'
}

function Update-ComposeForDockerDesktop {
    # Docker Desktop on Windows does not support network_mode: host. Comment
    # it out and uncomment the port mappings. Mirrors what the bash script
    # does on macOS via sed.
    $path = Join-Path $InstallPath 'docker-compose.yml'
    Info 'Rewriting compose for Docker Desktop (no host networking)...'

    $content = [System.IO.File]::ReadAllText($path)
    Copy-Item $path "$path.bak" -Force

    $content = $content -replace '(?m)^(\s*)network_mode: host', '#$1network_mode: host'
    $content = $content -replace '(?m)^(\s*)#ports:', '$1ports:'
    $content = $content -replace '(?m)^(\s*)#(\s*)- "\$\{PORT:-8000\}:8000"', '$1$2- "$${PORT:-8000}:8000"'

    [System.IO.File]::WriteAllText($path, $content)
    Warn 'Printer auto-discovery (SSDP) does NOT work on Docker Desktop. Add printers manually by IP.'
    Warn 'Virtual Printer ports (322, 990, 2024-2026, 3000/3002, 6000, 8883, 50000-50100) stay commented out.'
    Warn 'If you plan to use a Virtual Printer, edit docker-compose.yml and uncomment the relevant `- "PORT:PORT"` lines under `ports:`.'
}

function Start-Bambuddy {
    Info 'Starting Bambuddy container...'
    if ($Build) {
        & cmd /c "$DockerCompose up -d --build"
    } else {
        & cmd /c "$DockerCompose up -d"
    }
    if ($LASTEXITCODE -ne 0) {
        Fail 'Container start failed. Inspect logs with:'
        Write-Host "    cd $InstallPath" -ForegroundColor Yellow
        Write-Host "    $DockerCompose logs bambuddy" -ForegroundColor Yellow
        exit 1
    }

    Info 'Waiting for container to be Up...'
    $attempts = 0
    while ($attempts -lt 15) {
        Start-Sleep -Seconds 2
        $ps = & cmd /c "$DockerCompose ps" 2>&1
        if ($ps -match 'Up') { Ok 'Bambuddy container is running'; return }
        if ($ps -match 'Exited') {
            Fail 'Container exited unexpectedly.'
            Write-Host "    $DockerCompose logs bambuddy" -ForegroundColor Yellow
            exit 1
        }
        $attempts++
    }
    Warn "Container may still be starting. Check with: $DockerCompose ps"
}

# --- Main -----------------------------------------------------------------

if ($Help) { Show-Help }

Write-Banner

Info 'Detecting environment...'
Test-Docker

# Defaults
if (-not $InstallPath) { $InstallPath = Read-Default 'Installation directory' (Join-Path $env:USERPROFILE 'bambuddy') }
if (-not $Port)        { $Port = [int](Read-Default 'Port to expose' '8000') }
if (-not $TimeZone)    {
    $detected = Get-DefaultTimeZone
    $TimeZone = Read-Default 'Timezone (IANA)' $detected
}
if (-not $Build -and -not $Yes) {
    if (Read-YesNo 'Build from source? (No = use pre-built image)' 'n') { $Build = $true }
}

Write-Host ''
Write-Host 'Installation Summary' -ForegroundColor Cyan
Write-Host '-----------------------------------------'
Write-Host "  Install path:  $InstallPath"
Write-Host "  Port:          $Port"
Write-Host "  Timezone:      $TimeZone"
Write-Host "  Build source:  $Build"
Write-Host ''

if (-not (Read-YesNo 'Proceed with installation?' 'y')) {
    Write-Host 'Cancelled.' -ForegroundColor Yellow
    exit 0
}

Initialize-InstallDir
Get-ComposeFile
Write-EnvFile
Update-ComposeForDockerDesktop
Start-Bambuddy

$lanIp = Get-LanIp

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Green
Write-Host '   Installation Complete!' -ForegroundColor Green
Write-Host '==========================================================' -ForegroundColor Green
Write-Host ''
Write-Host "  Access Bambuddy:  http://localhost:$Port" -ForegroundColor Cyan
Write-Host "                    http://${lanIp}:$Port  (from other devices)" -ForegroundColor Cyan
Write-Host ''
Write-Host '  Manage container:'
Write-Host "    Status:   cd `"$InstallPath`"; $DockerCompose ps"
Write-Host "    Logs:     cd `"$InstallPath`"; $DockerCompose logs -f bambuddy"
Write-Host "    Stop:     cd `"$InstallPath`"; $DockerCompose down"
Write-Host "    Start:    cd `"$InstallPath`"; $DockerCompose up -d"
Write-Host "    Restart:  cd `"$InstallPath`"; $DockerCompose restart"
Write-Host ''
Write-Host '  Update Bambuddy:'
if ($Build) {
    Write-Host "    cd `"$InstallPath`"; git pull; $DockerCompose up -d --build"
} else {
    Write-Host "    cd `"$InstallPath`"; $DockerCompose pull; $DockerCompose up -d"
}
Write-Host ''
Write-Host '  Documentation:    https://wiki.bambuddy.cool' -ForegroundColor Cyan
Write-Host ''
Write-Host '  Note: Printer discovery is unavailable on Docker Desktop.' -ForegroundColor Yellow
Write-Host '        Add your printers manually by IP address in the UI.' -ForegroundColor Yellow
Write-Host ''
