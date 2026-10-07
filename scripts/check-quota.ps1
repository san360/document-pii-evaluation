<#
.SYNOPSIS
    Preprovision check: can the chosen region actually host what we are about
    to ask for?

.DESCRIPTION
    Run by azd before anything is created. A failure here costs nothing; the
    same failure discovered half way through a deployment leaves a partial
    resource group behind.

    Checks performed:
      * the Azure CLI is present and logged in
      * the chosen region offers every resource kind being deployed
      * the F0 Document Intelligence trap is called out loudly
      * an existing F0 Language account in the subscription is reported,
        because there can be only one
#>
[CmdletBinding()]
param(
    [string]$Location = $env:AZURE_LOCATION,
    [string]$Subscription = $env:AZURE_SUBSCRIPTION_ID
)

$ErrorActionPreference = 'Stop'

function Write-Step { param($Message) Write-Host "  $Message" -ForegroundColor Gray }
function Write-Good { param($Message) Write-Host "  OK    $Message" -ForegroundColor Green }
function Write-Warn { param($Message) Write-Host "  WARN  $Message" -ForegroundColor Yellow }
function Write-Bad  { param($Message) Write-Host "  FAIL  $Message" -ForegroundColor Red }

Write-Host ''
Write-Host 'Preprovision checks' -ForegroundColor Cyan

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Bad 'Azure CLI (az) not found. Install it: https://aka.ms/installazurecli'
    exit 1
}

$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Bad 'Not logged in. Run: az login'
    exit 1
}
Write-Good "Signed in to '$($account.name)' as $($account.user.name)"

if ($Subscription -and $account.id -ne $Subscription) {
    Write-Step "Switching to subscription $Subscription"
    az account set --subscription $Subscription | Out-Null
}

if (-not $Location) {
    Write-Warn 'AZURE_LOCATION is not set yet; skipping the region check.'
    Write-Host ''
    exit 0
}

# --- Which resources are actually being deployed? --------------------------
function Test-Flag {
    param([string]$Name, [bool]$Default = $true)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value -ieq 'true'
}

$wantLanguage = Test-Flag 'DEPLOY_LANGUAGE'
$wantStorage  = Test-Flag 'DEPLOY_STORAGE'

$parts = @()
if ($wantLanguage) { $parts += 'Language' }
if ($wantStorage)  { $parts += 'Storage' }
Write-Step ("Deploying: " + ($parts -join ', '))

if (-not $wantLanguage) {
    Write-Warn 'Language is off. The Document PII API cannot be called without it.'
}
if (-not $wantStorage) {
    Write-Warn 'Storage is off. Document PII reads its source from a blob and cannot run without one.'
}

# --- Region availability ---------------------------------------------------
$failed = $false

foreach ($kind in @(
    @{ Name = 'Language'; Kind = 'TextAnalytics'; Want = $wantLanguage }
)) {
    if (-not $kind.Want) { continue }

    $skus = az cognitiveservices account list-skus --kind $kind.Kind --location $Location 2>$null | ConvertFrom-Json
    if (-not $skus -or $skus.Count -eq 0) {
        Write-Bad "$($kind.Name) ($($kind.Kind)) is not available in '$Location'."
        $failed = $true
    }
    else {
        $names = ($skus | ForEach-Object { $_.name } | Sort-Object -Unique) -join ', '
        Write-Good "$($kind.Name) available in '$Location' (SKUs: $names)"
    }
}

if ($wantStorage) {
    $regions = az account list-locations --query "[?name=='$Location'].name" -o tsv 2>$null
    if (-not $regions) {
        Write-Bad "'$Location' is not a region in this subscription."
        $failed = $true
    }
    else {
        Write-Good "Storage available in '$Location'"
    }
}

# --- The expensive mistakes ------------------------------------------------
if ($wantLanguage -and $env:LANGUAGE_SKU -eq 'F0') {
    $existing = az cognitiveservices account list `
        --query "[?kind=='TextAnalytics' && sku.name=='F0'].{name:name,rg:resourceGroup}" -o json 2>$null | ConvertFrom-Json
    if ($existing -and $existing.Count -gt 0) {
        $names = ($existing | ForEach-Object { "$($_.name) (in $($_.rg))" }) -join ', '
        Write-Bad "LANGUAGE_SKU is F0 but this subscription already has one: $names"
        Write-Bad 'Only one F0 Language account is allowed per subscription. Use S.'
        $failed = $true
    }
}

Write-Host ''
if ($failed) {
    Write-Host 'Preprovision checks failed — nothing was created.' -ForegroundColor Red
    exit 1
}

Write-Host 'Preprovision checks passed.' -ForegroundColor Green
Write-Host ''
exit 0
