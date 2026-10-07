# 3 — Managed identity end to end

**Status:** partially possible today · **Depends on:** story 1

## Story

Run with no keys anywhere — not in `.env`, not in Key Vault, not in the
container's environment.

## Why

Keys are the default today because they are the shortest path to a working
call. They are also long-lived, copied by hand, and present in a file that is
one `.gitignore` mistake away from being committed.

## Acceptance

- [ ] `AZURE_USE_ENTRA_ID=true` is the documented default, and key auth is the
      fallback rather than the other way round.
- [ ] The signed-in principal is granted `Cognitive Services User` on the
      Language resource and `Storage Blob Data Contributor` on the storage
      account. (Both already happen in `infra/resources.bicep` when
      `AZURE_PRINCIPAL_ID` is set.)
- [ ] Storage has `allowSharedKeyAccess: false`; SAS generation uses a user
      delegation key only.
- [ ] Token acquisition is cached and refreshed rather than fetched per call.
- [ ] The Diagnostics tab reports which auth mode is live, which principal is
      being used, and names the missing role assignment when a call returns 403.
- [ ] `.env.example` leads with the Entra ID path.

## Notes

- The plumbing already exists in `evaluate_document_pii.py`: `language_headers()`
  (line ~262) switches between `Ocp-Apim-Subscription-Key` and a bearer token on
  `config.auth`, and `sas_credentials()` (line ~285) already falls back to a user
  delegation key when the blob service has no account key. What is missing is
  making Entra the default and caching the token.
- **A user delegation key cannot outlive 7 days**, and this tool mints 2-day
  SAS tokens because the service requires ≥24 h (story 8). Those constraints
  are compatible, but only just — do not lengthen the SAS lifetime without
  re-checking this.
- Scope is `https://cognitiveservices.azure.com/.default` for the Language
  resource.
- The failure mode to design for: the most common 403 here is someone who owns
  the subscription and assumes that is enough. `Owner` is a control-plane role
  and grants nothing on the data plane. The error message should say that
  outright.
- Note that `az role assignment list` can return `[]` for a principal that
  genuinely holds roles, when the caller lacks permission to read assignments.
  Do not use an empty list as evidence that a grant is missing — check the
  deployment operations instead.
- `DefaultAzureCredential` acquires a token per call today, which is both slow
  and rate-limited. Cache it with its expiry.
- There is no hosted compute in scope, so there is no managed identity to
  assign — this story is about the **local** developer path using `az login`.
  A user-assigned identity only becomes relevant if hosting is ever added,
  which story README lists as explicitly out of scope.
