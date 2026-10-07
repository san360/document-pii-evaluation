# 1 — Azure infrastructure as code (resources only, no hosting)

**Status:** done · **Depends on:** nothing · **Priority:** —

## Story

Provision the Azure resources this app needs from one command, behind per-resource
flags, so a second environment is an `azd up` rather than a morning of portal
clicking — and so the app can be pointed at a fresh set of resources without
hand-copying keys.

**Scope is resources only.** No Container App, no App Service, no hosting of any
kind. The app keeps running locally; only what it *calls* is deployed.

## Why

The resources are created by hand today. That is fine for one developer on one
laptop and unacceptable for anything else: there is no record of what the working
configuration is, no way to recreate it, and no way to tell whether two
environments differ.

Copying three endpoints and three keys out of the portal into `.env` by hand is
also the most likely place to make a silent mistake — a key pasted with a
trailing space fails with a 401 that explains nothing.

## Acceptance

- [ ] `infra/main.bicep` declares **only**:
      - a single-service **Language** resource (`kind: TextAnalytics`) — see Notes
      - a **Storage account** with the `pii-source` and `pii-target` containers
- [ ] **No compute resource of any kind is declared.** A reviewer should be able
      to confirm that by reading the file.
- [ ] Each resource sits behind its own flag:
      ```bicep
      param deployLanguage bool = true
      param deployStorage bool = true
      ```
- [ ] Region is a parameter, defaulting to one where both are available.
- [ ] Outputs expose every value `.env.example` asks for.
- [ ] `azure.yaml` wires `azd up`, with a `preprovision` hook that checks regional
      availability and quota **before** anything is created.
- [ ] **A `postprovision` hook writes the local `.env` from the deployment
      outputs**, so the gap between "provisioned" and "running locally" is zero
      manual steps. It must refuse to overwrite an existing `.env` without
      `-Force`, and must write `.env` (gitignored), never `.env.example`.
- [ ] `scripts/write-env.ps1` does the same against an existing deployment, so the
      file can be regenerated without redeploying.
- [ ] `azd down` removes everything, including the storage containers.
- [ ] README gains a "Provision your own" section making the no-hosting scope
      explicit.

## Notes

- The Language resource should be **single-service** (`kind: TextAnalytics`), not
  a multi-service `AIServices` account: the native document API is documented
  against the former. The endpoint shape is identical either way, so this cannot
  be checked after the fact from the URL — it has to be right at creation.
- Storage needs no public access. The app generates SAS URLs itself, so the
  containers stay private.
- The `.env` writer should emit the same commented structure as `.env.example`
  rather than a bare `KEY=value` dump. The comments are where the auth-mode
  choice is explained; a generated file that drops them loses the only place a
  reader would find it.
- Pairs with story 3: when `AZURE_USE_ENTRA_ID=true` is chosen, the writer should
  emit endpoints and leave the key lines empty rather than fetching keys at all.
- *(Historical: this story originally also provisioned a Document Intelligence
  resource, with a note that it must be S0 because F0 silently OCRs only the
  first two pages. That resource was removed along with the OCR path — the
  Document PII service does its own extraction.)*

---

## Outcome

Implemented. `infra/main.bicep` + `infra/resources.bicep`, wired through
`azure.yaml` with `scripts/check-quota.ps1` (preprovision) and
`scripts/write-env.ps1` (postprovision).

**Deployed live** to a resource group in `swedencentral`: Language (`TextAnalytics`,
S0) + storage account with both containers, and no compute resource of any
kind. Verified end to end — the app runs against it.

- `deployStorage=false` creates the resource group and the Language account only.
- `scripts/check-quota.ps1` confirmed availability against the real subscription.
- `tests/test_env_contract.py` runs the real generator and asserts its keys
  match the engine exactly. It immediately caught one drift:
  `AZURE_STORAGE_ACCOUNT_URL` was missing from `.env.example`.

Two things the deployment taught that the acceptance list did not anticipate:

**Keys are not Bicep outputs.** Deployment outputs are retained in the resource
group's deployment history and readable by anyone with Reader, so a key placed
there outlives any attempt to remove it. `write-env.ps1` fetches keys from the
data plane instead.

**Azure Policy blocked two things that look like defaults.** `disableLocalAuth`
is forced to `true`, and storage `publicNetworkAccess` to `Disabled`. Both are
now set explicitly in Bicep, and the resources carry the `SecurityControl=Ignore`
tag that exempts them. Without the tag the deployment fails with a policy error
that does not name the offending property.
