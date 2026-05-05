param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$Location,

    [Parameter(Mandatory = $true)]
    [string]$EnvironmentName,

    [Parameter(Mandatory = $true)]
    [string]$ContainerAppName,

    [Parameter(Mandatory = $true)]
    [string]$AcrName,

    [string]$ImageTag = "latest"
)

$ErrorActionPreference = "Stop"

function Write-Info([string]$message) {
    Write-Host "[step1] $message"
}

Write-Info "Setter subscription"
az account set --subscription $SubscriptionId | Out-Null

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..")
$imageName = "$AcrName.azurecr.io/cvmatcher:$ImageTag"

Write-Info "Sikrer resource group"
az group create --name $ResourceGroup --location $Location | Out-Null

Write-Info "Sikrer ACR"
az acr create --resource-group $ResourceGroup --name $AcrName --sku Basic --admin-enabled true 2>$null | Out-Null

Write-Info "Bygger og pusher container image"
az acr build --registry $AcrName --image "cvmatcher:$ImageTag" "$repoRoot" | Out-Null

Write-Info "Sikrer Container Apps environment"
az containerapp env create --name $EnvironmentName --resource-group $ResourceGroup --location $Location 2>$null | Out-Null

$acrUser = az acr credential show --name $AcrName --query "username" -o tsv
$acrPassword = az acr credential show --name $AcrName --query "passwords[0].value" -o tsv

Write-Info "Oppretter/oppdaterer Container App"
az containerapp create `
    --name $ContainerAppName `
    --resource-group $ResourceGroup `
    --environment $EnvironmentName `
    --image $imageName `
    --target-port 8000 `
    --ingress external `
    --min-replicas 0 `
    --max-replicas 1 `
    --registry-server "$AcrName.azurecr.io" `
    --registry-username $acrUser `
    --registry-password $acrPassword `
    --env-vars `
        "USE_OPENAI_ANALYSIS=true" `
        "ENABLE_CINODE_PUBLISH=true" `
        "CINODE_UI_AUTOMATION_ENABLED=true" `
        "CINODE_UI_HEADLESS=true" `
        "CORS_ALLOW_ORIGINS=*" 2>$null | Out-Null

$fqdn = az containerapp show --name $ContainerAppName --resource-group $ResourceGroup --query "properties.configuration.ingress.fqdn" -o tsv
Write-Info "Ferdig. URL: https://$fqdn"
