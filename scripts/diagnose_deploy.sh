#!/usr/bin/env bash
# Called by the deploy workflow when a step fails: prints revision state and container logs.
# Signs in with the workflow's own OIDC token (no secret).
set -uo pipefail
TOKEN=$(curl -sSf -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=api://AzureADTokenExchange" | jq -r .value)
az login --service-principal -u "$AZURE_CLIENT_ID" -t "$AZURE_TENANT_ID" --federated-token "$TOKEN" -o none
az account set -s "$AZURE_SUBSCRIPTION_ID"
APP=$(azd env get-value SERVICE_API_NAME 2>/dev/null || true)
[ -z "$APP" ] && { echo "no container app yet"; exit 0; }
echo "::group::revisions"
az containerapp revision list -n "$APP" -g "$AZURE_RESOURCE_GROUP" -o table \
  --query '[].{name:name, active:properties.active, health:properties.healthState, running:properties.runningState}'
echo "::endgroup::"
echo "::group::system log"
az containerapp logs show -n "$APP" -g "$AZURE_RESOURCE_GROUP" --type system --tail 60 || true
echo "::endgroup::"
echo "::group::console log"
az containerapp logs show -n "$APP" -g "$AZURE_RESOURCE_GROUP" --type console --tail 200 || true
echo "::endgroup::"
