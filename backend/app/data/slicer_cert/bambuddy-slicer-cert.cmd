@echo off
rem Bambuddy: let Bambu Studio and OrcaSlicer connect to this Bambuddy's
rem virtual printers. Double-click this file and confirm the administrator
rem prompt. It adds this Bambuddy's certificate to the slicers' printer.cer,
rem and installs a task that adds it again after a slicer update.
rem
rem   bambuddy-slicer-cert.cmd /uninstall   removes that task again
rem
rem The PowerShell code below the marker line does the work.
setlocal
set "BAMBUDDY_SELF=%~f0"
set "BAMBUDDY_ARGS=%*"
rem One line, so cmd.exe has read it all before PowerShell runs: /uninstall
rem deletes the installed copy of this file.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s = [IO.File]::ReadAllText($env:BAMBUDDY_SELF); $m = '#BAMBUDDY' + '_POWERSHELL'; Invoke-Expression $s.Substring($s.IndexOf($m))" && exit /b 0 || exit /b 1
#BAMBUDDY_POWERSHELL
# Made by Bambuddy for the certificate with SHA-256 fingerprint
# __BAMBUDDY_CA_FINGERPRINT__
# Written for Windows PowerShell 5.1, which every Windows 10 and 11 has.

$ErrorActionPreference = 'Stop'

$CaPem = @'
__BAMBUDDY_CA_PEM__
'@

$Fingerprint = '__BAMBUDDY_CA_FINGERPRINT__'
$Id = ($Fingerprint -replace ':', '').Substring(0, 8)
$TaskName = "Bambuddy slicer certificate $Id"
# Under Program Files, where only administrators can create or change
# anything: the task runs the copy kept there as SYSTEM.
$InstallRoot = if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'Bambuddy slicer certificate' } else { $null }
$InstallDir = if ($InstallRoot) { Join-Path $InstallRoot $Id } else { $null }
$Arguments = " $env:BAMBUDDY_ARGS ".ToLowerInvariant()
$Quiet = $Arguments.Contains(' /quiet ')
$Uninstall = $Arguments.Contains(' /uninstall ')
# Only for Bambuddy's own tests: look for printer.cer under these folders,
# and do nothing that needs Windows (administrator rights, the task).
$TestRoots = $env:BAMBUDDY_CERT_TEST_ROOTS
$SystemBases = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramW6432) | Where-Object { $_ } |
    ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') + '\' } | Sort-Object -Unique

function Write-Line([string]$Text) {
    if ($Quiet) {
        $script:Log += $Text + "`r`n"
    } else {
        Write-Host $Text
    }
}

function Get-CertBodies([string]$Text) {
    # The base64 text of each certificate, without line breaks: the same
    # certificate gives the same string however the file wraps its lines.
    $pattern = '-----BEGIN CERTIFICATE-----([A-Za-z0-9+/=\s]+?)-----END CERTIFICATE-----'
    foreach ($match in [regex]::Matches($Text, $pattern)) {
        [pscustomobject]@{
            Body  = ($match.Groups[1].Value -replace '\s', '')
            Match = $match
        }
    }
}

function Get-Subject([string]$Body) {
    try {
        $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(, [Convert]::FromBase64String($Body))
        return $cert.Subject
    } catch {
        return $null
    }
}

function Find-CertFiles {
    if ($TestRoots) {
        foreach ($root in $TestRoots.Split(';')) {
            Get-ChildItem -LiteralPath $root -Recurse -Filter printer.cer -File -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName }
        }
        return
    }
    # Only administrators can write below Program Files. The task runs as
    # SYSTEM and must never write where a user could plant a link, so it
    # changes nothing else; a run by hand also covers other install folders.
    $folders = @()
    foreach ($base in $SystemBases) {
        $folders += Join-Path $base 'Bambu Studio'
        $folders += Join-Path $base 'OrcaSlicer'
    }
    # Installed somewhere else: the uninstall entries say where.
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    if (-not $Quiet) {
        $keys += 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    }
    foreach ($key in $keys) {
        Get-ItemProperty -Path $key -ErrorAction SilentlyContinue |
            Where-Object { $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -match 'Bambu ?Studio|Orca ?Slicer' } |
            ForEach-Object {
                if ($_.PSObject.Properties['InstallLocation'] -and $_.InstallLocation) {
                    $folders += $_.InstallLocation.Trim('"')
                }
                if ($_.PSObject.Properties['DisplayIcon'] -and $_.DisplayIcon) {
                    $folders += Split-Path -Parent (($_.DisplayIcon -split ',')[0].Trim('"'))
                }
            }
    }
    if (-not $Quiet -and $env:LOCALAPPDATA) {
        # Installed for this user only.
        Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs') -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match 'Bambu ?Studio|Orca ?Slicer' } |
            ForEach-Object { $folders += $_.FullName }
    }

    $folders | Where-Object { $_ } | ForEach-Object {
        Join-Path $_ 'resources\cert\printer.cer'
    } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        ForEach-Object { [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $_).ProviderPath) } |
        Where-Object {
            $path = $_
            -not $Quiet -or (Test-UnderProgramFiles $path)
        } |
        Sort-Object -Unique
}

function Test-UnderProgramFiles([string]$Path) {
    return [bool]($SystemBases | Where-Object { $Path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) })
}

function Test-Link([string]$Path) {
    # True when the file or a folder above it is a link (symbolic link,
    # junction): writing through it could change a file somewhere else.
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    while ($item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $true }
        $item = if ($item -is [IO.FileInfo]) { $item.Directory } else { $item.Parent }
    }
    return $false
}

function Update-CertFile([string]$Path) {
    $ours = (Get-CertBodies $CaPem | Select-Object -First 1).Body
    if (-not $TestRoots -and (Test-Link $Path)) {
        Write-Line "  skipped, the path contains a link: $Path"
        return $true
    }
    $text = [IO.File]::ReadAllText($Path)
    $certs = @(Get-CertBodies $text)
    if ($certs | Where-Object { $_.Body -eq $ours }) {
        Write-Line "  already there: $Path"
        return $true
    }
    try {
        # A slicer uses the first certificate with the right name, so an
        # earlier issue of this CA (same name, same key, other dates) would
        # hide this one: it goes. Not the bare legacy name: older Bambuddy
        # installs all share it, and it may be another one's.
        $subject = Get-Subject $ours
        if ($subject -eq 'CN=Virtual Printer CA') { $subject = $null }
        $replaced = $false
        $newText = $text
        foreach ($cert in ($certs | Sort-Object { $_.Match.Index } -Descending)) {
            if ($subject -and (Get-Subject $cert.Body) -eq $subject) {
                $newText = $newText.Remove($cert.Match.Index, $cert.Match.Length)
                $replaced = $true
            }
        }
        $newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
        $newText = $newText.TrimEnd() + $newline + ($CaPem.Trim() -replace "`r?`n", $newline) + $newline

        $backup = "$Path.before-bambuddy"
        if (-not (Test-Path -LiteralPath $backup)) {
            Copy-Item -LiteralPath $Path -Destination $backup
        }
        # Written next to the file and then moved over it, so the slicer
        # never sees half a file.
        $tmp = "$Path.bambuddy-new"
        [IO.File]::WriteAllText($tmp, $newText, (New-Object System.Text.UTF8Encoding($false)))
        try {
            # One step on NTFS, and keeps the file's permissions.
            # [NullString]: PowerShell would pass $null as "", which Replace refuses.
            [IO.File]::Replace($tmp, $Path, [NullString]::Value)
        } catch {
            Move-Item -LiteralPath $tmp -Destination $Path -Force
        }
        if ($replaced) {
            Write-Line "  replaced an older Bambuddy certificate: $Path"
        } else {
            Write-Line "  added: $Path"
        }
        return $true
    } catch {
        Write-Line "  FAILED: $Path ($($_.Exception.Message))"
        return $false
    }
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Install-Task {
    # The task runs this copy as SYSTEM. It lives under Program Files,
    # whose permissions it inherits: only administrators can change it.
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    if (Test-Link $InstallDir) { throw "$InstallDir contains a link" }
    $copy = Join-Path $InstallDir 'bambuddy-slicer-cert.cmd'
    if ([IO.Path]::GetFullPath($env:BAMBUDDY_SELF) -ne [IO.Path]::GetFullPath($copy)) {
        Copy-Item -LiteralPath $env:BAMBUDDY_SELF -Destination $copy -Force
    }

    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$copy`" /quiet`""
    $triggers = @(
        (New-ScheduledTaskTrigger -AtStartup),
        (New-ScheduledTaskTrigger -AtLogOn),
        (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650))
    )
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings `
        -Description 'Adds the Bambuddy certificate to Bambu Studio and OrcaSlicer again after a slicer update.' -Force | Out-Null
}

$script:Log = ''
$exitCode = 0
try {
    if (-not $TestRoots -and -not (Test-Admin)) {
        Write-Host 'Bambuddy needs administrator rights to change the slicers. Please confirm the prompt.'
        $elevated = @{ FilePath = $env:BAMBUDDY_SELF; Verb = 'RunAs' }
        if ($env:BAMBUDDY_ARGS) { $elevated.ArgumentList = $env:BAMBUDDY_ARGS }
        Start-Process @elevated
        exit 0
    }

    if ($Uninstall) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Get-ChildItem -LiteralPath $InstallRoot -Force -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $InstallRoot -Force -ErrorAction SilentlyContinue
        }
        Write-Line 'The automatic check is removed. The certificate stays in the slicers.'
    } else {
        Write-Line 'Bambuddy slicer certificate'
        Write-Line ''
        $files = @(Find-CertFiles)
        $failed = $false
        foreach ($file in $files) {
            if (-not (Update-CertFile $file)) { $failed = $true }
        }
        Write-Line ''
        if ($files.Count -eq 0) {
            Write-Line 'No Bambu Studio or OrcaSlicer installation found.'
            $exitCode = 2
        } elseif ($failed) {
            Write-Line 'Not every slicer could be updated, see above.'
            $exitCode = 1
        } elseif (-not $TestRoots -and (Get-Process -Name 'bambu-studio', 'orca-slicer' -ErrorAction SilentlyContinue)) {
            Write-Line 'Done. A slicer is running: close it completely and start it again.'
        } else {
            Write-Line 'Done. The slicer uses the certificate the next time it starts.'
        }

        if (-not $Quiet -and -not $TestRoots) {
            Install-Task
            Write-Line ''
            Write-Line 'Windows now checks this every hour and at every start, and adds the'
            Write-Line 'certificate again after a slicer update. To stop that, run this file'
            Write-Line 'with /uninstall.'
            $elsewhere = @($files | Where-Object { -not (Test-UnderProgramFiles $_) })
            if ($elsewhere.Count -gt 0) {
                Write-Line ''
                Write-Line 'The automatic check only changes slicers under Program Files. Run this'
                Write-Line 'file again after updating the slicer in:'
                foreach ($file in $elsewhere) { Write-Line "  $file" }
            }
        }
    }
} catch {
    Write-Line "Error: $($_.Exception.Message)"
    $exitCode = 1
}

if ($Quiet -and -not $TestRoots) {
    try {
        [IO.File]::WriteAllText((Join-Path $InstallDir 'last-run.log'), ((Get-Date).ToString('s') + "`r`n" + $script:Log))
    } catch { }
}
if (-not $Quiet -and -not $TestRoots) {
    Read-Host 'Press Enter to close' | Out-Null
}
exit $exitCode
