// Resource-group-scoped resources for Document PII Redaction Studio.
//
// NO COMPUTE IS DECLARED IN THIS FILE. See the header of main.bicep.

@description('Region for all resources.')
param location string

@description('Deterministic suffix derived from the subscription and environment name.')
@minLength(13)
param resourceToken string

param deployLanguage bool
param deployStorage bool

param languageSku string
param storageSku string

param sourceContainerName string
param targetContainerName string

@description('Principal to grant data-plane roles to. Empty skips role assignment.')
param principalId string = ''

@description('Tags applied to every resource, including SecurityControl=Ignore.')
param tags object = {}

// Built-in role definition ids.
// 'Cognitive Services User' — the data-plane role. Owner/Contributor are
// control-plane roles and grant nothing on these endpoints, which is the most
// common cause of a 403 that people spend an afternoon on.
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

// ---------------------------------------------------------------------------
// Azure AI Language — serves BOTH PII APIs:
//   POST /language/:analyze-text             (Text PII, synchronous)
//   POST /language/analyze-documents/jobs    (Document PII, asynchronous)
//
// Deliberately kind 'TextAnalytics' (single-service Language), NOT 'AIServices'
// (multi-service Foundry). The native document API is documented against the
// single-service resource. Both expose an identical *.cognitiveservices.azure.com
// endpoint, so this cannot be verified after the fact from the URL — it has to
// be right at creation.
// ---------------------------------------------------------------------------
resource language 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (deployLanguage) {
  name: 'lang-${resourceToken}'
  location: location
  kind: 'TextAnalytics'
  sku: {
    name: languageSku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: 'lang-${resourceToken}'
    publicNetworkAccess: 'Enabled'
    // Keys still work; this simply allows Entra ID as well, so
    // AZURE_USE_ENTRA_ID=true needs no redeploy.
    //
    // A subscription policy may override this to true regardless. The
    // SecurityControl=Ignore tag exempts the resource from that policy; without
    // it, no key can be issued and Entra ID becomes the only option.
    disableLocalAuth: false
  }
  tags: union(tags, {
    purpose: 'pii-detection'
  })
}

// ---------------------------------------------------------------------------
// Storage — required ONLY by the native Document PII API, which cannot accept
// an uploaded file: it reads its source from a blob and writes its artifacts
// to a container you own.
//
// Containers stay private. The app mints short-lived SAS URLs itself and hands
// those to the service, so nothing here is publicly reachable.
// ---------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (deployStorage) {
  name: 'st${resourceToken}'
  location: location
  kind: 'StorageV2'
  sku: {
    name: storageSku
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    // Left true because the app's default auth mode generates SAS from the
    // account key. Story 3 (managed identity) flips this to false and moves
    // SAS generation onto a user delegation key.
    allowSharedKeyAccess: true
    // Set explicitly: a subscription policy otherwise disables the data plane
    // entirely, and both this app AND the Document PII service need to reach
    // the blobs — the service reads the source and writes its output itself.
    // 'allowBlobPublicAccess: false' above still keeps every container private;
    // this only governs whether the endpoint is reachable at all.
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      // Lets the Language service reach the account as a trusted Azure service.
      bypass: 'AzureServices'
      ipRules: []
      virtualNetworkRules: []
    }
  }
  tags: union(tags, {
    purpose: 'document-pii-staging'
  })
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = if (deployStorage) {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      // Staged sources are deleted after every run. A short window is enough
      // to recover from a mistake without keeping document-derived data around.
      enabled: true
      days: 1
    }
  }
}

resource sourceContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (deployStorage) {
  parent: blobService
  name: sourceContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource targetContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (deployStorage) {
  parent: blobService
  name: targetContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// Data-plane role assignments, so AZURE_USE_ENTRA_ID=true works without any
// further setup. Skipped when principalId is empty.
// ---------------------------------------------------------------------------
resource languageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployLanguage && !empty(principalId)) {
  name: guid(language.id, principalId, cognitiveServicesUser)
  scope: language
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
    principalId: principalId
  }
}

resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployStorage && !empty(principalId)) {
  name: guid(storage.id, principalId, storageBlobDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: principalId
  }
}

// -- Outputs (no keys — see main.bicep) -------------------------------------
// The `!` asserts the resource exists; it is only dereferenced inside the same
// condition that created it.
output languageEndpoint string = deployLanguage ? language!.properties.endpoint : ''
output languageName string = deployLanguage ? language!.name : ''
output storageAccountName string = deployStorage ? storage!.name : ''
output storageAccountUrl string = deployStorage ? storage!.properties.primaryEndpoints.blob : ''
