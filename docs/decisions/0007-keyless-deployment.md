# 0007 · Keyless deployment: GitHub OIDC and managed identity

**Context.** API keys in CI variables or app settings leak.

**Decision.** GitHub Actions authenticates to Azure with OpenID Connect (federated credential, no client secret). The Container App uses a user-assigned managed identity for Azure OpenAI, Speech, Language, Content Safety, Key Vault and the registry, with local keys disabled where the service allows it. The only secrets are third-party ones (Suisse Line tool key, webhook secret, calendar credentials), held in Key Vault.

**Consequences.** One-time setup needs an Azure owner to run a bootstrap script in Cloud Shell. Infrastructure is declared in Bicep and deployed with `azd`, so the environment can be rebuilt from the repository.
