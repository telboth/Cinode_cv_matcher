# Steg 1 - Intern drift på Azure Container Apps

Dette er en enkel, billig driftspakke for intern bruk uten Entra-login i appen.

## Forutsetninger

- Azure CLI installert og innlogget (`az login`)
- Rettigheter til å opprette RG/ACR/Container App
- `OPENAI_API_KEY` og `CINODE_API_TOKEN` settes etter deploy som secrets i Container App

## Kjør deploy

Fra repo-roten:

```powershell
.\deploy\azure\deploy-step1-containerapp.ps1 `
  -SubscriptionId "<sub-id>" `
  -ResourceGroup "rg-cvmatcher-int" `
  -Location "northeurope" `
  -EnvironmentName "cae-cvmatcher-int" `
  -ContainerAppName "cvmatcher-int" `
  -AcrName "cvmatcherintacr" `
  -ImageTag "v1"
```

Scriptet:

- bygger Docker image
- pusher image til ACR
- oppretter Container Apps environment
- oppretter Container App med `min-replicas=0` (skalerer til null når ubrukt)

## Sett secrets (etter deploy)

```powershell
az containerapp secret set `
  --name cvmatcher-int `
  --resource-group rg-cvmatcher-int `
  --secrets `
    openai-api-key="<OPENAI_API_KEY>" `
    cinode-api-token="<CINODE_API_TOKEN>"
```

Knytt secrets til env vars:

```powershell
az containerapp update `
  --name cvmatcher-int `
  --resource-group rg-cvmatcher-int `
  --set-env-vars `
    OPENAI_API_KEY=secretref:openai-api-key `
    CINODE_API_TOKEN=secretref:cinode-api-token
```

## DNS (intern URL)

Når appen er oppe, peker DNS `cvmatcher.int.xlent.no` til Container App FQDN (CNAME).
