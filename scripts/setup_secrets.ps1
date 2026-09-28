# Upload secrets from .env to GCP Secret Manager
# Run from project root: .\scripts\setup_secrets.ps1

$ENV_FILE = ".env"
$SECRETS = @(
    "LINE_CHANNEL_ACCESS_TOKEN",
    "LINE_CHANNEL_SECRET",
    "GEMINI_API_KEY",
    "GOOGLE_MAPS_API_KEY"
)

if (-not (Test-Path $ENV_FILE)) {
    Write-Error ".env not found. Run from project root."
    exit 1
}

$envLines = Get-Content $ENV_FILE

foreach ($key in $SECRETS) {
    $line = $envLines | Where-Object { $_ -match "^'?$key'?\s*=" }
    if (-not $line) {
        Write-Warning "[$key] not found in .env, skipping."
        continue
    }

    $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
    if (-not $value) {
        Write-Warning "[$key] value is empty, skipping."
        continue
    }

    Write-Host "Processing $key ..." -ForegroundColor Cyan

    $existing = gcloud secrets describe $key 2>$null
    if (-not $existing) {
        Write-Host "  Creating secret: $key" -ForegroundColor Yellow
        gcloud secrets create $key --replication-policy=automatic | Out-Null
    }

    # 用 Out-File -NoNewline 避免 BOM 和 \r\n（secret 值均為純 ASCII）
    $tempFile = "$env:TEMP\ramen_secret_$key.txt"
    $value | Out-File -FilePath $tempFile -NoNewline -Encoding ASCII
    gcloud secrets versions add $key --data-file=$tempFile 2>&1 | Out-Null
    Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    Write-Host "  [OK] $key uploaded" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done! Secrets in Secret Manager:" -ForegroundColor Magenta
gcloud secrets list
