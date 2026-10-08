[CmdletBinding()]
param(
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvironmentFile = Join-Path $ProjectRoot ".env.impactx"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed or is not available on PATH. Install Docker Desktop, then rerun this script."
}

docker compose version | Out-Null

if (-not (Test-Path -LiteralPath $EnvironmentFile)) {
    function New-HexSecret([int]$ByteCount) {
        $bytes = New-Object byte[] $ByteCount
        [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        return [Convert]::ToHexString($bytes).ToLowerInvariant()
    }

    $DemoPassword = "ImpactX-" + (New-HexSecret 12)
    $Users = @{ admin = @{ password = $DemoPassword; role = "admin" } } | ConvertTo-Json -Compress
    $Lines = @(
        "ECDAT_DB_PASSWORD=$(New-HexSecret 32)",
        "ECDAT_TOKEN_SECRET=$(New-HexSecret 32)",
        "ECDAT_USERS_JSON=$Users"
    )
    Set-Content -LiteralPath $EnvironmentFile -Value $Lines -Encoding utf8NoBOM
    Write-Host "Created private ImpactX environment: .env.impactx"
    Write-Host "Demo username: admin"
    Write-Host "Demo password: $DemoPassword"
    Write-Host "Save these credentials now; the launcher will not print the stored password again."
} else {
    Write-Host "Using existing private ImpactX environment: .env.impactx"
}

Push-Location $ProjectRoot
try {
    docker compose --env-file $EnvironmentFile config --quiet
    $Arguments = @("compose", "--env-file", $EnvironmentFile, "up", "-d", "--wait")
    if ($Rebuild) { $Arguments += "--build" }
    docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed with exit code $LASTEXITCODE." }
    Write-Host "ECDAT is ready at http://127.0.0.1:3000"
    Write-Host "Health: http://127.0.0.1:8000/ready"
    Write-Host "Stop safely with: docker compose --env-file .env.impactx down"
} finally {
    Pop-Location
}
