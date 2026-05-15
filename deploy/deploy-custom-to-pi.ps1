param(
    [Parameter(Mandatory = $true)]
    [string]$HostName,
    [Parameter(Mandatory = $true)]
    [string]$User,
    [string]$RemoteDir = "~/bambuddy"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$archive = Join-Path $env:TEMP "bambuddy-custom-deploy.tar"

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Creating deployment archive..."
if (Test-Path $archive) {
    Remove-Item -LiteralPath $archive -Force
}

Push-Location $repoRoot
try {
    Invoke-Native tar `
        --exclude=".git/objects" `
        --exclude=".git/logs" `
        --exclude=".git/hooks" `
        --exclude=".git/index" `
        --exclude=".venv" `
        --exclude="frontend/node_modules" `
        --exclude="data" `
        --exclude="logs" `
        --exclude=".pip-tmp" `
        --exclude=".coverage" `
        -cf $archive .
}
finally {
    Pop-Location
}

Write-Host "Preparing remote folder on $User@$HostName..."
Invoke-Native ssh "$User@$HostName" "mkdir -p $RemoteDir"

Write-Host "Uploading source archive..."
Invoke-Native scp $archive "${User}@${HostName}:$RemoteDir/bambuddy-custom-deploy.tar"

Write-Host "Extracting and rebuilding Bambuddy on Raspberry Pi..."
Invoke-Native ssh "$User@$HostName" "cd $RemoteDir && tar -xf bambuddy-custom-deploy.tar && rm bambuddy-custom-deploy.tar && mkdir -p .git && printf 'ref: refs/heads/main\n' > .git/HEAD && docker compose up -d --build"

Write-Host ""
Write-Host "Deploy complete. Bambuddy should be available at:"
Write-Host "http://${HostName}:8000"
Write-Host ""
Write-Host "Useful checks:"
Write-Host "ssh $User@$HostName `"cd $RemoteDir && docker compose logs -f bambuddy`""
