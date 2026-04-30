param location string = resourceGroup().location
param acrName string = 'acr2${uniqueString(resourceGroup().id)}'
param envName string = 'aca2-env-${uniqueString(resourceGroup().id)}'
param appName string = 'med-ai-bot' // Updated name

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${envName}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' } }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Step 1: Create the app as a BACKGROUND WORKER (No Ingress) ──
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: appName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      // INGRESS REMOVED ENTIRELY. This is a background polling bot.
      activeRevisionsMode: 'Single'
    }
    template: {
      containers: [
        {
          name: 'med-bot'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5') // Increased slightly for PyTorch/YOLO overhead
            memory: '1.0Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1 // CRITICAL: Must be 1 so the bot constantly polls Telegram
        maxReplicas: 1
      }
    }
  }
}

var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, acr.id, containerApp.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}