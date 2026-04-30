param location string = resourceGroup().location
param acrName string = 'acr${uniqueString(resourceGroup().id)}'
param envName string = 'aca1-env-${uniqueString(resourceGroup().id)}'
param appName string = 'flask-demo-app' // Updated name

// 1. Provision the Azure Container Registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false // We don't need this anymore with Managed Identity!
  }
}

// 2. ACA requires a Log Analytics workspace
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${envName}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
  }
}

// 3. Provision the Container Apps Environment
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

// --- THE MAGIC HAPPENS HERE ---

// 4. Provision the Container App WITH a Managed Identity
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned' // <--- This creates the identity for the app
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 5000 // Standard Flask port
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system' // <--- Tells the app to use its Managed Identity
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'flask-app'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' //just for the first image 
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// 5. Grant the Container App permission to pull from the ACR
var acrPullRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // This is the fixed ID for AcrPull

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, acr.id, containerApp.id, 'AcrPull') // Unique name for the assignment
  scope: acr // Scope the permission to just this ACR
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: containerApp.identity.principalId // Give the permission to the App's identity
    principalType: 'ServicePrincipal'
  }
}
