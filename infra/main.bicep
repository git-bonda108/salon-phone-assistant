// Lina – salon phone assistant · Azure infrastructure (Switzerland North only)
//
// Resource-group scoped: the deploy identity only has rights on this one resource group.
// Keyless: one user-assigned managed identity for the registry, Key Vault, AI Services
// (Azure OpenAI, Content Safety, Language) and, optionally, Microsoft Graph.
// Model deployments are *Standard* (regional): requests are processed in Switzerland.

targetScope = 'resourceGroup'

@description('Swiss regions only (ADR 0004).')
@allowed([ 'switzerlandnorth' ])
param location string = 'switzerlandnorth'

@description('azd environment name, used for tags and names.')
param environmentName string

@description('Object id of the salon owner (Key Vault access to read the generated tool key / webhook secret).')
param ownerObjectId string = ''

@description('Chat model for the agents (regional Standard deployment in Switzerland North).')
param chatModel string = 'gpt-4.1-mini'
param chatModelVersion string = '2025-04-14'
param chatCapacity int = 30

@description('Create/update the model deployments. The pipeline sets false once they exist: repeated writes to a model deployment can be refused by Azure for new subscriptions.')
param deployModels bool = true

@description('Optional stronger model for the call reviewer / evaluation judge. Needs regional quota.')
param deployJudge bool = false
param judgeModel string = 'gpt-5.1'
param judgeModelVersion string = '2025-11-13'
param judgeCapacity int = 10

@description('sqlite (demo) | google | outlook')
param calendarBackend string = 'sqlite'
param graphAuth string = 'delegated'
param graphClientId string = ''
@description('JSON: {"lara": {"user": "me", "calendar_id": "..."}, ...}; empty = none')
param graphCalendars string = ''

@description('Public read-only demo with synthetic data (the interview deployment). Set false for a real salon.')
param demoMode bool = true

@description('Image set by azd deploy; a public placeholder is used for the very first provision.')
param apiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var tags = { 'azd-env-name': environmentName, app: 'salon-phone-assistant', 'data-residency': 'CH' }

// Built-in role definition ids
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  kvSecretsOfficer: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
  cogServicesOpenAIUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  cogServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-lina-${token}'
  location: location
  tags: tags
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-lina-${token}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-lina-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id, DisableLocalAuth: false }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crlina${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.acrPull)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-lina-${token}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// The app generates its own secrets on first start (tool key, admin token, webhook secret).
resource vaultApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, roles.kvSecretsOfficer)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsOfficer)
  }
}

resource vaultOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(ownerObjectId)) {
  name: guid(vault.id, ownerObjectId, roles.kvSecretsOfficer)
  scope: vault
  properties: {
    principalId: ownerObjectId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsOfficer)
  }
}

// One Foundry (AI Services) account: Azure OpenAI + Content Safety + Language, Entra-only.
resource ai 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'ais-lina-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'ais-lina-${token}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: ai
  name: 'lina'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Lina – salon phone assistant', description: 'Agents, evaluation and guardrails' }
  // The account accepts one change at a time: create the project after the model deployments.
  dependsOn: [ chat, judge ]
}

resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (deployModels) {
  parent: ai
  name: chatModel
  sku: { name: 'Standard', capacity: chatCapacity } // regional, not Global/DataZone
  properties: { model: { format: 'OpenAI', name: chatModel, version: chatModelVersion } }
}

resource judge 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (deployModels && deployJudge) {
  parent: ai
  name: judgeModel
  sku: { name: 'Standard', capacity: judgeCapacity }
  properties: { model: { format: 'OpenAI', name: judgeModel, version: judgeModelVersion } }
  dependsOn: [ chat ]
}

resource aiOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ai.id, identity.id, roles.cogServicesOpenAIUser)
  scope: ai
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cogServicesOpenAIUser)
  }
}

resource aiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ai.id, identity.id, roles.cogServicesUser)
  scope: ai
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cogServicesUser)
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-lina-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-lina-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [ { server: registry.properties.loginServer, identity: identity.id } ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'KEY_VAULT_URL', value: vault.properties.vaultUri }
            { name: 'AZURE_AI_ENDPOINT', value: 'https://${ai.properties.customSubDomainName}.cognitiveservices.azure.com' }
            { name: 'AZURE_OPENAI_ENDPOINT', value: 'https://${ai.properties.customSubDomainName}.openai.azure.com' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: chatModel }
            { name: 'AZURE_OPENAI_JUDGE_DEPLOYMENT', value: deployJudge ? judgeModel : chatModel }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
            { name: 'CALENDAR_BACKEND', value: calendarBackend }
            { name: 'GRAPH_AUTH', value: graphAuth }
            { name: 'GRAPH_CLIENT_ID', value: graphClientId }
            { name: 'GRAPH_CALENDARS', value: graphCalendars }
            { name: 'DATABASE_PATH', value: '/tmp/lina/app.db' }
            { name: 'OTEL_SERVICE_NAME', value: 'lina-api' }
            { name: 'PUBLIC_DEMO', value: demoMode ? '1' : '0' }
            { name: 'DEMO_SEED', value: demoMode ? '1' : '0' }
          ]
          probes: [
            // first start can wait for Key Vault RBAC to propagate: allow up to 5 minutes
            { type: 'Startup', httpGet: { path: '/health', port: 8000 }, periodSeconds: 10, failureThreshold: 30 }
            { type: 'Liveness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 10 }
          ]
        }
      ]
      // SQLite store: one replica (waitlist, offers, reviews). Calendar = source of truth.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [ acrPull, vaultApp, aiOpenAIUser, aiUser ]
}

output AZURE_LOCATION string = location
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.properties.loginServer
output AZURE_KEY_VAULT_NAME string = vault.name
output AZURE_AI_ENDPOINT string = 'https://${ai.properties.customSubDomainName}.cognitiveservices.azure.com'
output AZURE_AI_PROJECT string = project.name
output SERVICE_API_NAME string = app.name
output SERVICE_API_URI string = 'https://${app.properties.configuration.ingress.fqdn}'
output MANAGED_IDENTITY_CLIENT_ID string = identity.properties.clientId
