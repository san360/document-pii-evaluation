// Document PII Redaction Studio — resource provisioning.
//
// SCOPE: the services the app CALLS. Nothing that RUNS the app.
// There is deliberately no Container App, App Service, Function App, VM or
// any other compute resource declared here or in resources.bicep. The console
// runs on your machine; only its dependencies live in Azure. If that ever
// changes it needs its own decision, not an addition to this file.

targetScope = 'subscription'

@minLength(1)
@maxLength(40)
@description('Name of the environment. Used to derive resource names.')
param environmentName string

@minLength(1)
@description('Region for all resources. Must support Language and Storage.')
param location string

@description('Resource group to deploy into. Created if it does not exist.')
param resourceGroupName string = ''

// -- Per-resource flags -----------------------------------------------------
// Provision only what you intend to use. Someone redacting digital PDFs with
// Text PII needs neither Document Intelligence nor Storage.

@description('Azure AI Language. Required by BOTH PII APIs. Turning this off leaves nothing to call.')
param deployLanguage bool = true

@description('Storage account and containers. Required ONLY by the native Document PII API, which reads a source blob and writes its artifacts to a container you own.')
param deployStorage bool = true

// -- SKUs -------------------------------------------------------------------

@description('Language SKU. F0 is the free tier: one per subscription, shared across the whole Language family.')
@allowed(['F0', 'S'])
param languageSku string = 'S'

@description('Storage replication. LRS is correct here: the blobs are transient staging copies, deleted after each run.')
@allowed(['Standard_LRS', 'Standard_ZRS', 'Standard_GRS'])
param storageSku string = 'Standard_LRS'

@description('Blob container the app stages source chunks into.')
param sourceContainerName string = 'pii-source'

@description('Blob container the Document PII service writes its artifacts to.')
param targetContainerName string = 'pii-target'

@description('Object id of the principal to grant data-plane roles to. Leave empty to skip role assignment and use keys instead.')
param principalId string = ''

@description('Tags applied to the resource group and every resource. SecurityControl=Ignore exempts these resources from the subscription policy that forces disableLocalAuth=true, which would otherwise make key authentication impossible.')
param tags object = {
  SecurityControl: 'Ignore'
}

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var rgName = empty(resourceGroupName) ? 'rg-${environmentName}' : resourceGroupName

var allTags = union(tags, {
  'azd-env-name': environmentName
  application: 'doc-redaction'
})

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: rgName
  location: location
  tags: allTags
}

module resources 'resources.bicep' = {
  name: 'doc-redaction-resources'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
    deployLanguage: deployLanguage
    deployStorage: deployStorage
    languageSku: languageSku
    storageSku: storageSku
    sourceContainerName: sourceContainerName
    targetContainerName: targetContainerName
    principalId: principalId
    tags: allTags
  }
}

// -- Outputs ----------------------------------------------------------------
// Endpoints and names only. Keys are deliberately NOT output: deployment
// outputs are retained in the resource group's deployment history and readable
// by anyone with Reader on it. scripts/write-env.ps1 fetches keys from the
// data plane instead, so they exist only in your local .env.

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location

output AZURE_LANGUAGE_ENDPOINT string = resources.outputs.languageEndpoint
output AZURE_LANGUAGE_NAME string = resources.outputs.languageName

output AZURE_STORAGE_ACCOUNT_NAME string = resources.outputs.storageAccountName
output AZURE_STORAGE_ACCOUNT_URL string = resources.outputs.storageAccountUrl
output AZURE_STORAGE_SOURCE_CONTAINER string = sourceContainerName
output AZURE_STORAGE_TARGET_CONTAINER string = targetContainerName

output DEPLOYED_LANGUAGE bool = deployLanguage
output DEPLOYED_STORAGE bool = deployStorage
