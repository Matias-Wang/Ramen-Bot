# One-time setup: Workload Identity Federation for GitHub Actions
# Run ONCE. Then add the two output values to GitHub Secrets:
#   https://github.com/Matias-Wang/Ramen-Bot/settings/secrets/actions
#
# Usage: .\scripts\setup_github_actions.ps1

$PROJECT_ID    = "gen-lang-client-0685910295"
$GITHUB_REPO   = "Matias-Wang/Ramen-Bot"
$SA_NAME       = "github-actions-sa"
$SA_EMAIL      = "$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
$POOL_NAME     = "github-actions-pool"
$PROVIDER_NAME = "github-provider"

Write-Host "Step 1: Creating Service Account..." -ForegroundColor Green
gcloud iam service-accounts create $SA_NAME `
    --project=$PROJECT_ID `
    --display-name="GitHub Actions Deployer"

Write-Host "Step 2: Granting IAM roles..." -ForegroundColor Green
gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$SA_EMAIL" `
    --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$SA_EMAIL" `
    --role="roles/iam.serviceAccountUser"

gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$SA_EMAIL" `
    --role="roles/artifactregistry.writer"

# 已完成初次設定的環境：只需單獨執行以下這段 E2E 授權（2026-09-28 已執行）。
# E2E 測試（.github/workflows/e2e.yml）需要：讀寫 Firestore，
# 以及讀取 Gemini / Maps 金鑰。金鑰僅對這兩個 secret 個別授權，
# 不開放 LINE 憑證等其他 secret。
gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$SA_EMAIL" `
    --role="roles/datastore.user"

foreach ($SECRET in @("GEMINI_API_KEY", "GOOGLE_MAPS_API_KEY")) {
    gcloud secrets add-iam-policy-binding $SECRET `
        --project=$PROJECT_ID `
        --member="serviceAccount:$SA_EMAIL" `
        --role="roles/secretmanager.secretAccessor"
}

Write-Host "Step 3: Creating Workload Identity Pool..." -ForegroundColor Green
gcloud iam workload-identity-pools create $POOL_NAME `
    --project=$PROJECT_ID `
    --location="global" `
    --display-name="GitHub Actions Pool"

$POOL_ID = gcloud iam workload-identity-pools describe $POOL_NAME `
    --project=$PROJECT_ID `
    --location="global" `
    --format="value(name)"

Write-Host "Step 4: Creating OIDC Provider..." -ForegroundColor Green
gcloud iam workload-identity-pools providers create-oidc $PROVIDER_NAME `
    --project=$PROJECT_ID `
    --location="global" `
    --workload-identity-pool=$POOL_NAME `
    --display-name="GitHub Actions OIDC Provider" `
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" `
    --attribute-condition="assertion.repository == '$GITHUB_REPO'" `
    --issuer-uri="https://token.actions.githubusercontent.com"

Write-Host "Step 5: Binding WIF to Service Account..." -ForegroundColor Green
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL `
    --project=$PROJECT_ID `
    --role="roles/iam.workloadIdentityUser" `
    --member="principalSet://iam.googleapis.com/$POOL_ID/attribute.repository/$GITHUB_REPO"

$WIF_PROVIDER = gcloud iam workload-identity-pools providers describe $PROVIDER_NAME `
    --project=$PROJECT_ID `
    --location="global" `
    --workload-identity-pool=$POOL_NAME `
    --format="value(name)"

Write-Host ""
Write-Host "=== DONE: Add these two values to GitHub Secrets ===" -ForegroundColor Cyan
Write-Host "WIF_PROVIDER        = $WIF_PROVIDER" -ForegroundColor Yellow
Write-Host "WIF_SERVICE_ACCOUNT = $SA_EMAIL" -ForegroundColor Yellow
Write-Host ""
Write-Host "GitHub Secrets URL:" -ForegroundColor Cyan
Write-Host "  https://github.com/$GITHUB_REPO/settings/secrets/actions" -ForegroundColor Cyan
