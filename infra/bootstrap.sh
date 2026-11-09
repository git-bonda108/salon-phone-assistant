#!/usr/bin/env bash
# One-time Azure setup for keyless GitHub deployment. Run in Azure Cloud Shell (Bash)
# as an Owner of the subscription. Safe to re-run (idempotent).
#
#   curl -sSL https://raw.githubusercontent.com/git-bonda108/salon-phone-assistant/main/infra/bootstrap.sh | bash
#
# What it does
#   1. registers the resource providers the app needs
#   2. creates the resource group in Switzerland North
#   3. creates a deploy identity (app registration) that GitHub Actions can use through
#      OpenID Connect: a federated credential for this repo's main branch, NO client secret
#   4. grants that identity rights on this ONE resource group only
#      (Contributor + Role Based Access Control Administrator, needed to assign the app's
#      managed-identity roles)
#   5. creates a public-client app for connecting an Outlook / Microsoft 365 calendar
#      (delegated Calendars.ReadWrite, personal and work accounts)
#   6. prints the values to add as GitHub repository *variables* (none of them is a secret)
set -euo pipefail

REPO="${REPO:-git-bonda108/salon-phone-assistant}"
LOCATION="${LOCATION:-switzerlandnorth}"
RG="${RG:-rg-lina-salon}"
DEPLOY_APP="${DEPLOY_APP:-gh-${REPO//\//-}-deploy}"
OUTLOOK_APP="${OUTLOOK_APP:-Salon Lina - Outlook calendar connector}"
GRAPH_API="00000003-0000-0000-c000-000000000000"
CALENDARS_READWRITE_DELEGATED="1ec239c2-d7c9-4623-a91a-a9775856bb36"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
say "Subscription $(az account show --query name -o tsv) ($SUB_ID)"

say "1/6 Registering resource providers"
for p in Microsoft.App Microsoft.ContainerRegistry Microsoft.CognitiveServices Microsoft.KeyVault \
         Microsoft.OperationalInsights Microsoft.Insights Microsoft.ManagedIdentity; do
  az provider register --namespace "$p" --wait >/dev/null && echo "  $p"
done

say "2/6 Resource group $RG in $LOCATION"
az group create --name "$RG" --location "$LOCATION" --tags app=salon-phone-assistant data-residency=CH -o none
RG_ID=$(az group show --name "$RG" --query id -o tsv)

say "3/6 Deploy identity (OIDC, no secret)"
APP_ID=$(az ad app list --display-name "$DEPLOY_APP" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then APP_ID=$(az ad app create --display-name "$DEPLOY_APP" --query appId -o tsv); fi
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv 2>/dev/null || az ad sp create --id "$APP_ID" --query id -o tsv)
# GitHub sends either the classic subject (repo:owner/repo:…) or, for newer repositories and
# organisations, the immutable form with numeric ids (repo:owner@<id>/repo@<id>:…). Trust both.
SUBJECTS=("github-main|repo:${REPO}:ref:refs/heads/main")
GH_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}" 2>/dev/null || true)
if [ -n "$GH_JSON" ]; then
  GH_OWNER_ID=$(echo "$GH_JSON" | jq -r '.owner.id')
  GH_REPO_ID=$(echo "$GH_JSON" | jq -r '.id')
  SUBJECTS+=("github-main-ids|repo:${REPO%%/*}@${GH_OWNER_ID}/${REPO##*/}@${GH_REPO_ID}:ref:refs/heads/main")
fi
for entry in "${SUBJECTS[@]}"; do
  name="${entry%%|*}"; subject="${entry#*|}"
  if ! az ad app federated-credential list --id "$APP_ID" --query "[?name=='${name}']" -o tsv | grep -q .; then
    az ad app federated-credential create --id "$APP_ID" -o none --parameters "{
      \"name\": \"${name}\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"${subject}\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }"
  fi
  echo "  trusts ${subject}"
done
echo "  app id $APP_ID"

say "4/6 Least privilege: rights on $RG only"
for role in "Contributor" "Role Based Access Control Administrator"; do
  az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
    --role "$role" --scope "$RG_ID" -o none 2>/dev/null || true
  echo "  $role"
done

say "5/6 Outlook calendar connector (public client, delegated Calendars.ReadWrite)"
GRAPH_CLIENT_ID=$(az ad app list --display-name "$OUTLOOK_APP" --query "[0].appId" -o tsv)
if [ -z "$GRAPH_CLIENT_ID" ]; then
  GRAPH_CLIENT_ID=$(az ad app create --display-name "$OUTLOOK_APP" --is-fallback-public-client true \
    --public-client-redirect-uris "https://login.microsoftonline.com/common/oauth2/nativeclient" --query appId -o tsv)
fi
GRAPH_APP_OBJECT_ID=$(az ad app show --id "$GRAPH_CLIENT_ID" --query id -o tsv)
# Personal (outlook.com) + work accounts: v2 access tokens first, then the audience.
# Set through Microsoft Graph, because a new app has no 'api' section for `az ad app update --set`.
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/${GRAPH_APP_OBJECT_ID}" \
  --headers "Content-Type=application/json" --body '{"api": {"requestedAccessTokenVersion": 2}}' -o none
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/${GRAPH_APP_OBJECT_ID}" \
  --headers "Content-Type=application/json" \
  --body '{"signInAudience": "AzureADandPersonalMicrosoftAccount", "isFallbackPublicClient": true}' -o none
if ! az ad app permission list --id "$GRAPH_CLIENT_ID" \
     --query "[?resourceAppId=='${GRAPH_API}'].resourceAccess[].id" -o tsv | grep -q "$CALENDARS_READWRITE_DELEGATED"; then
  az ad app permission add --id "$GRAPH_CLIENT_ID" --api "$GRAPH_API" \
    --api-permissions "${CALENDARS_READWRITE_DELEGATED}=Scope" -o none 2>/dev/null || true
fi
echo "  client id $GRAPH_CLIENT_ID (personal + work accounts, Calendars.ReadWrite delegated)"

say "6/6 Values for GitHub → Settings → Secrets and variables → Actions → Variables"
OWNER_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || echo "")
cat <<EOF
  AZURE_CLIENT_ID        = $APP_ID
  AZURE_TENANT_ID        = $TENANT_ID
  AZURE_SUBSCRIPTION_ID  = $SUB_ID
  AZURE_RESOURCE_GROUP   = $RG
  AZURE_LOCATION         = $LOCATION
  AZURE_OWNER_OBJECT_ID  = $OWNER_ID
  GRAPH_CLIENT_ID        = $GRAPH_CLIENT_ID
EOF

if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  say "gh is signed in: setting the variables on $REPO"
  gh variable set AZURE_CLIENT_ID -R "$REPO" -b "$APP_ID"
  gh variable set AZURE_TENANT_ID -R "$REPO" -b "$TENANT_ID"
  gh variable set AZURE_SUBSCRIPTION_ID -R "$REPO" -b "$SUB_ID"
  gh variable set AZURE_RESOURCE_GROUP -R "$REPO" -b "$RG"
  gh variable set AZURE_LOCATION -R "$REPO" -b "$LOCATION"
  gh variable set AZURE_OWNER_OBJECT_ID -R "$REPO" -b "$OWNER_ID"
  gh variable set GRAPH_CLIENT_ID -R "$REPO" -b "$GRAPH_CLIENT_ID"
fi
say "Done. Push to main (or run the 'deploy' workflow) to deploy."
