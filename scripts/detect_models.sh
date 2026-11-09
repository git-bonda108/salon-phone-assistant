#!/usr/bin/env bash
# Sets DEPLOY_MODELS=false in the workflow environment when the chat model deployment already
# exists, so provisioning does not re-write it (Azure can refuse repeated writes for new subscriptions).
set -uo pipefail
TOKEN=$(curl -sSf -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=api://AzureADTokenExchange" | jq -r .value)
az login --service-principal -u "$AZURE_CLIENT_ID" -t "$AZURE_TENANT_ID" --federated-token "$TOKEN" -o none
az account set -s "$AZURE_SUBSCRIPTION_ID"
ACC=$(az cognitiveservices account list -g "$AZURE_RESOURCE_GROUP" --query "[?kind=='AIServices'].name | [0]" -o tsv 2>/dev/null)
if [ -n "$ACC" ] && az cognitiveservices account deployment show -g "$AZURE_RESOURCE_GROUP" -n "$ACC" \
     --deployment-name "${CHAT_MODEL:-gpt-4.1-mini}" -o none 2>/dev/null; then
  echo "model deployment exists on $ACC: leaving it untouched"
  echo "DEPLOY_MODELS=false" >> "$GITHUB_ENV"
else
  echo "no model deployment yet: it will be created"
  echo "DEPLOY_MODELS=true" >> "$GITHUB_ENV"
fi
