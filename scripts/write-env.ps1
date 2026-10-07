<#
.SYNOPSIS
    Write the local .env from a deployment, so there is nothing to copy by hand.

.DESCRIPTION
    Run automatically by azd after provisioning, and usable on its own against
    an existing resource group.

    Endpoints come from the deployment outputs. Keys are fetched here, from the
    data plane, rather than being emitted as Bicep outputs — deployment outputs
    are kept in the resource group's deployment history and are readable by
    anyone with Reader on it, so a key placed there outlives any attempt to
    remove it.

    The generated file keeps the comments from .env.example. They are the only
    place the F0 two-page trap and the auth-mode choice are explained, and a
    bare KEY=value dump loses that.

.PARAMETER ResourceGroup
    Read from this resource group instead of the current azd environment.

.PARAMETER UseEntraId
    Write AZURE_USE_ENTRA_ID=true and leave the key lines empty. No keys are
    fetched at all.

.PARAMETER Force
    Overwrite an existing .env. Without this the script refuses and exits 0, so
    a re-provision never silently discards local edits.

.EXAMPLE
    ./scripts/write-env.ps1
    ./scripts/write-env.ps1 -ResourceGroup rg-pii-dev -Force
    ./scripts/write-env.ps1 -UseEntraId
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup,
    [switch]$UseEntraId,
    [switch]$Force,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
if (-not $OutputPath) { $OutputPath = Join-Path $root '.env' }

function Write-Good { param($m) Write-Host "  OK    $m" -ForegroundColor Green }
function Write-Step { param($m) Write-Host "  $m" -ForegroundColor Gray }
function Write-Warn { param($m) Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Write-Bad  { param($m) Write-Host "  FAIL  $m" -ForegroundColor Red }

Write-Host ''
Write-Host 'Writing local .env from the deployment' -ForegroundColor Cyan

# --- Gather values ---------------------------------------------------------
$values = @{}

if (-not $ResourceGroup -and (Get-Command azd -ErrorAction SilentlyContinue)) {
    Write-Step 'Reading the current azd environment'
    $raw = azd env get-values --output json 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) {
        $parsed = $raw | ConvertFrom-Json
        foreach ($property in $parsed.PSObject.Properties) {
            $values[$property.Name] = [string]$property.Value
        }
    }
}

if ($ResourceGroup) {
    Write-Step "Reading resource group '$ResourceGroup'"
    $values['AZURE_RESOURCE_GROUP'] = $ResourceGroup

    $accounts = az cognitiveservices account list -g $ResourceGroup -o json 2>$null | ConvertFrom-Json
    foreach ($account in $accounts) {
        if ($account.kind -eq 'TextAnalytics') {
            $values['AZURE_LANGUAGE_NAME'] = $account.name
            $values['AZURE_LANGUAGE_ENDPOINT'] = $account.properties.endpoint
        }
    }

    $storage = az storage account list -g $ResourceGroup -o json 2>$null | ConvertFrom-Json
    if ($storage -and $storage.Count -gt 0) {
        $values['AZURE_STORAGE_ACCOUNT_NAME'] = $storage[0].name
        $values['AZURE_STORAGE_ACCOUNT_URL'] = $storage[0].primaryEndpoints.blob
    }
}

$resourceGroupName = $values['AZURE_RESOURCE_GROUP']
if (-not $resourceGroupName) {
    Write-Bad 'No resource group found. Run `azd provision`, or pass -ResourceGroup.'
    exit 1
}

$languageEndpoint = $values['AZURE_LANGUAGE_ENDPOINT']
$languageName     = $values['AZURE_LANGUAGE_NAME']
$storageName      = $values['AZURE_STORAGE_ACCOUNT_NAME']
$storageUrl       = $values['AZURE_STORAGE_ACCOUNT_URL']
$sourceContainer  = if ($values['AZURE_STORAGE_SOURCE_CONTAINER']) { $values['AZURE_STORAGE_SOURCE_CONTAINER'] } else { 'pii-source' }
$targetContainer  = if ($values['AZURE_STORAGE_TARGET_CONTAINER']) { $values['AZURE_STORAGE_TARGET_CONTAINER'] } else { 'pii-target' }

# --- Fetch keys ------------------------------------------------------------
# A subscription policy can force disableLocalAuth=true on Cognitive Services
# accounts, which blocks key retrieval entirely. That is a legitimate and
# common hardening posture, not an error — detect it and switch to Entra ID
# rather than writing a file with empty keys that silently cannot authenticate.
$languageKey = ''
$storageConnection = ''
$localAuthBlocked = @()

if (-not $UseEntraId) {
    foreach ($account in @(
        @{ Name = $languageName; Label = 'Language' }
    )) {
        if (-not $account.Name) { continue }
        $disabled = az cognitiveservices account show -n $account.Name -g $resourceGroupName `
            --query 'properties.disableLocalAuth' -o tsv 2>$null
        if ($disabled -eq 'true' -or $disabled -eq 'True') {
            $localAuthBlocked += "$($account.Label) ($($account.Name))"
        }
    }

    if ($localAuthBlocked.Count -gt 0) {
        Write-Host ''
        Write-Warn 'Key authentication is disabled on:'
        foreach ($item in $localAuthBlocked) { Write-Warn "  - $item" }
        Write-Warn 'A subscription policy sets disableLocalAuth=true, so no key can be'
        Write-Warn 'issued. Switching this .env to Entra ID authentication instead.'
        Write-Warn 'Make sure you have run `az login`; the deployment already granted'
        Write-Warn "your principal 'Cognitive Services User' on these resources."
        Write-Host ''
        $UseEntraId = $true
    }
}

if ($UseEntraId) {
    Write-Step 'Entra ID mode — no keys fetched'
}
else {
    if ($languageName) {
        $languageKey = az cognitiveservices account keys list -n $languageName -g $resourceGroupName --query key1 -o tsv 2>$null
        if ($languageKey) { Write-Good "Language key retrieved ($languageName)" }
        else { Write-Warn "Could not read the Language key for $languageName" }
    }
}

# The storage connection string is fetched in both modes: the app can still use
# an account key for SAS generation even when the AI resources require Entra ID,
# and allowSharedKeyAccess is a separate setting from disableLocalAuth.
if ($storageName) {
    $storageConnection = az storage account show-connection-string -n $storageName -g $resourceGroupName --query connectionString -o tsv 2>$null
    if ($storageConnection) { Write-Good "Storage connection string retrieved ($storageName)" }
    else { Write-Warn "Could not read the connection string for $storageName — SAS will use a user delegation key" }
}

# --- Refuse to clobber -----------------------------------------------------
if ((Test-Path $OutputPath) -and -not $Force) {
    Write-Host ''
    Write-Warn "$OutputPath already exists and was left untouched."
    Write-Warn 'Re-run with -Force to overwrite it, or merge these values yourself:'
    Write-Host ''
    Write-Host "  AZURE_LANGUAGE_ENDPOINT=$languageEndpoint" -ForegroundColor DarkGray
    Write-Host "  AZURE_STORAGE_ACCOUNT_URL=$storageUrl" -ForegroundColor DarkGray
    Write-Host ''
    exit 0
}

# --- Write it --------------------------------------------------------------
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')
$entraValue = if ($UseEntraId) { 'true' } else { 'false' }

$content = @"
# Generated by scripts/write-env.ps1 on $stamp UTC
# Resource group: $resourceGroupName
#
# This file is gitignored and holds live credentials. Regenerate it any time
# with:  ./scripts/write-env.ps1 -Force

# ---------------------------------------------------------------------------
# Azure AI Language — the Document PII API.
#   POST /language/analyze-documents/jobs
# ---------------------------------------------------------------------------
AZURE_LANGUAGE_ENDPOINT=$languageEndpoint
AZURE_LANGUAGE_KEY=$languageKey

# Default api-version; the UI and --api-version override it per run.
#   2026-05-01 (GA) | 2026-05-15-preview
# It decides which PII categories are accepted: both 2026 versions refuse 3 of
# the 177, and the API rejects the *whole request* over one unsupported one.
AZURE_DOCUMENT_PII_API_VERSION=2026-05-01

# ---------------------------------------------------------------------------
# Azure Blob Storage — required. The API cannot accept an uploaded file: it
# reads its source from a blob and writes its output to a container you own.
#
# The SAS this tool mints lasts two days. Anything under 24 hours is rejected
# with a generic "Invalid analysisInput in request" that names neither the SAS
# nor the expiry — an analyze-documents job carries its own 24-hour expiry, so
# the credentials have to outlive it.
# ---------------------------------------------------------------------------
AZURE_STORAGE_CONNECTION_STRING=$storageConnection
AZURE_STORAGE_ACCOUNT_URL=$storageUrl
AZURE_STORAGE_SOURCE_CONTAINER=$sourceContainer
AZURE_STORAGE_TARGET_CONTAINER=$targetContainer

# ---------------------------------------------------------------------------
# Auth mode. false uses the key above. true uses DefaultAzureCredential
# (az login) and ignores it — the deployment already granted your principal
# 'Cognitive Services User' on the Language resource and 'Storage Blob Data
# Contributor' on the storage account, provided principalId was supplied.
# ---------------------------------------------------------------------------
AZURE_USE_ENTRA_ID=$entraValue
"@

$content | Set-Content -Path $OutputPath -Encoding UTF8

Write-Host ''
Write-Good "Wrote $OutputPath"

$missing = @()
if (-not $languageEndpoint) { $missing += 'Language (the Document PII API is unavailable)' }
if (-not $storageUrl)       { $missing += 'Storage (the Document PII API cannot run without it)' }

if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Warn 'Not deployed, so left blank:'
    foreach ($item in $missing) { Write-Warn "  - $item" }
    Write-Warn 'The app starts anyway and will say which capability each one gates.'
}

Write-Host ''
Write-Host 'Start the app with:  ./run.ps1' -ForegroundColor Cyan
Write-Host ''
