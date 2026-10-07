# Document PII Evaluation

Test the **Azure AI Language Document PII API** against real PDFs — searchable,
scanned, or a mix — and see exactly what it gives back.

The only Azure call made is `analyze-documents/jobs`, plus the blob upload it
requires and the artifact download it produces. **No OCR, no text extraction, no
local redaction.** Whatever the service returns is the answer.

The one thing that does happen locally: each PDF is opened with PyMuPDF to label
it *searchable* or *scanned* for the results table. That label is cosmetic and
plays no part in the request — the same bytes are uploaded either way.

Everything runs on your machine. Nothing is deployed.

---

## What you get

For each document: did the job succeed, how long it took, how many PII entities
came back and in which categories, the **redacted file the service produced**,
and its own `result.json` verbatim.

```
  document                                       input       result         time   PII  top categories
  ---------------------------------------------  ----------  ----------- ------- -----  --------------------
  clinical-notes-it.pdf                       scanned     OK            24.5s    19  PersonType:5, Date:5…
  insurance-application.pdf  searchable  OK            29.9s   124  USDriversLicenseNum…
  medical-report-a.pdf                       scanned     OK            18.5s     7  Organization:5, Date…
  medical-report-b.pdf                                      scanned     OK            19.8s    10  Organization:4, Orga…

  By input format
    scanned      3/3 succeeded
    searchable   1/1 succeeded
```

Measured on `2026-05-01`. Scanned PDFs work — see the correction below.

## Two surfaces, one implementation

The browser console and the CLI both call `run_evaluation()` in
[`evaluate_document_pii.py`](evaluate_document_pii.py) with the same
`EvalOptions`, so they cannot send different requests. That matters: they once
built their own request bodies, and when the same document appeared to behave
differently depending on how it was submitted, the difference was assumed to be
in the request. It was not — but finding that out cost a day.

### Browser

```powershell
.\run.ps1          # http://127.0.0.1:8100
```

Drop documents, pick a mode, api-version and optionally a category subset, then
submit. You get a live log and a verdict table. **Click `view`** to see the
redacted document beside the original, with toggles for either alone.

### Command line

```powershell
python evaluate_document_pii.py --input docs                      # one job per file
python evaluate_document_pii.py --input docs --mode batch         # all files, one job
python evaluate_document_pii.py --input docs --api-version 2026-05-15-preview
python evaluate_document_pii.py --input docs --policy entityMask --auth entra
python evaluate_document_pii.py --input docs --categories Person Email PhoneNumber

# preview-only (2026-05-15-preview)
python evaluate_document_pii.py --input docs --api-version 2026-05-15-preview `
       --policy syntheticReplacement --preserve-data-format
python evaluate_document_pii.py --input docs --api-version 2026-05-15-preview `
       --synonyms "Person=Insured,Applicant,Life" "Address=Residence"
```

Both write to `evaluations/<timestamp>/` and both list each other's runs.

### Two modes, because they answer different questions

`per-document` (default) isolates failures so one bad file cannot mask the
others — what you want for evaluation. `batch` puts every file in one job, the
way the API is designed to be used, and answers whether one unprocessable
document takes the others down with it. (Measured: it does not.)

Batch is also meaningfully faster for the same work — **58.7 s for four files
against 92.7 s submitted separately** — and returned identical entity counts in
both modes, so the isolation that `per-document` buys costs wall-clock time and
nothing else.

---

## Setup

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env     # then fill it in
.\run.ps1
```

The console starts with nothing configured and tells you what is missing rather
than failing obscurely.

### What you need in Azure

| Resource | Why |
|---|---|
| **Azure AI Language** (single-service, `kind: TextAnalytics`) | The API itself. The native document endpoint is documented against a single-service resource, not a multi-service Foundry one. |
| **Storage account** + 2 containers | Required. The API cannot accept an uploaded file — it reads its source from a blob and writes output to a container you own. Containers are created on first use. |

Key auth by default; `AZURE_USE_ENTRA_ID=true` uses `az login` instead. Owning
the subscription is a control-plane role and grants nothing here — you need
`Cognitive Services User` on the Language resource and `Storage Blob Data
Contributor` on the storage account. `azd` grants both if you set
`AZURE_PRINCIPAL_ID`.

### Provision it

```powershell
azd auth login
azd env new pii-dev
azd up
```

**Resources only — nothing is hosted.** There is no container or App Service in
[`infra/`](infra/); the console stays on your machine. A `postprovision` hook
writes your local `.env` from the outputs, so there is nothing to copy by hand.

Regenerate `.env` later without redeploying:

```powershell
./scripts/write-env.ps1 -Force
./scripts/write-env.ps1 -ResourceGroup rg-pii-dev -Force
./scripts/write-env.ps1 -UseEntraId -Force      # endpoints only, no keys
```

Keys are deliberately **not** Bicep outputs — deployment outputs stay in the
resource group's history and are readable by anyone with Reader. They are
fetched from the data plane instead, so they exist only in your gitignored
`.env`.

---

## Five things the documentation does not tell you

All measured against a live resource. Full write-up in
[`stories/08-service-necessity.md`](stories/08-service-necessity.md).

**The SAS must be valid for at least 24 hours.** A shorter one is rejected with
`Invalid analysisInput in request / The request body is invalid or malformed` —
a message naming neither the SAS nor the expiry, identical to the error for a
genuinely malformed body. An `analyze-documents` job carries its own 24-hour
expiry, so the credentials have to outlive it. Measured by bisection: 23h
rejected, 24h accepted. This tool mints two-day SAS tokens.

**Document PII rejects the whole request over one bad category.** Of the 177 in
the taxonomy, both supported versions refuse the same 3 —
`CASocialIdentificationNumber`, `KRSocialSecurityNumber` and `VIN`. Unsupported
categories are dropped with a warning rather than failing the run. The matrix is
on the **Diagnostics** tab and was probed by
[`tests/discover_categories.py`](tests/discover_categories.py), not read from
docs.

**Categories are a real filter, not a display hint.** Measured on the same
scanned page with `2026-05-01`: all categories → 10 entities, `PhoneNumber`
alone → 1, `Person`+`PhoneNumber` → 2, `Person`+`Organization` → 7. Note that
asking for `Organization` also returns `OrganizationSports`. Pick them in the
**PII categories** panel on the Evaluate tab, or pass `--categories` on the CLI;
leave it untouched to detect everything. The three categories no api-version
accepts are struck through and excluded rather than sent.

**The retired `2024-11-15-preview` detected substantially less.** On that same
page it found 3 entities where `2026-05-01` finds 10, which is why this tool no
longer offers it.

**`result.json` is not shaped the way the PII docs imply.** The category field
is `type`, not `category` — reading `category` yields `"Unknown"` for every
entity. There is **no `offset`**, only a length, so an entity cannot be mapped
back to a position. And `text` is normalised: a date printed `08/15/2025` comes
back as `2025-08-15`.

> **Correction worth knowing.** This repo previously claimed Document PII
> *cannot* read scanned PDFs, from three scans that all failed with
> `InternalServerError`. All three fell inside a two-minute window — one
> transient fault sampled three times, not a capability limit. Re-tested: all
> three succeed. Document PII handles scanned PDFs.

---

## Two preview-only options

`2026-05-15-preview` is a strict superset of GA. Two of its additions are
implemented here; both are **refused on `2026-05-01`** rather than silently
dropped, because a downgraded policy would redact differently without saying so.

**`syntheticReplacement`** replaces PII with realistic fake values instead of
asterisks, which keeps a document readable for testing. With
`--preserve-data-format` the replacement keeps the original's shape — measured
on a test form, a name became another plausible name, an 8-digit phone number
became `1-383-696-1596 x8200`, and a date of birth became `1959-11-27`.

The same input maps to the same replacement within a run, so a value repeated
across pages stays consistent.

**`entitySynonyms`** adds context vocabulary — the labels sitting beside a value
on a form. It accepts **12 entity types**, not all 177. It widens detection; it
will not make the service recognise a value that does not look like the entity
at all — see *A name the service will not find* below.

Both are on the **Evaluate** tab, greyed out until you select the preview
version, and both are persisted into `summary.json` so a run records exactly
what it sent.

> **Note on `entityMask`.** The Learn comparison table lists *"Entity label
> masking policy"* as preview-only. It is not — `entityMask` is in the GA
> `RedactionPolicyKind` enum and works on `2026-05-01`. The other six rows in
> that table do match the REST spec.

## A name the service will not find

Detection is linguistic, so a placeholder that does not look like a name is not
treated as one. Measured on a filled insurance form whose names were template
placeholders of the form `<PlaceholderFirst> <PlaceholderLast>`:

| Value | Where | Detected |
|---|---|---|
| a realistic full name | body text | 1.00 ✅ |
| a realistic full name | **table cell** | 1.00 ✅ |
| real given name + placeholder surname | body text | 0.94 ✅ |
| real given name + placeholder surname | **table cell** | not found ❌ |
| placeholder given name + placeholder surname | anywhere | not found ❌ |

Tables are not the problem — a realistic name in a table cell scores 1.00 and is
redacted. A placeholder token simply is not name-like, and a real given name
paired with a placeholder surname is borderline enough (0.94 against 1.00) that
it survives in prose and fails in the terser context of a table cell. Neither
the preview version, `markerMask`, `entitySynonyms` nor a 0.3 confidence
threshold changed this.

**If you are evaluating recall, use realistic names.** Placeholder tokens
measure the model's tolerance for nonsense, not its accuracy.

---

## Layout

```
evaluate_document_pii.py     the engine — CLI entry point and shared core
data/entity_categories.json  177 PII categories + measured per-version support
run.ps1                      starts the console (checks the port first)
azure.yaml                   azd wiring: preprovision + postprovision hooks
app/
  main.py                    FastAPI: one API, static console, mtime-versioned assets
  config.py                  how the server runs (Azure config lives in the engine)
  routers/evaluate.py        thin HTTP surface over evaluate_document_pii.py
  services/jobs.py           in-memory progress for in-flight runs
  static/                    one CSS file, five classic scripts, no build step
  templates/index.html       Evaluate + Diagnostics tabs
infra/                       Bicep: Language + Storage, no compute by design
scripts/
  check-quota.ps1            preprovision: region and SKU checks
  write-env.ps1              postprovision: writes your local .env
design/flow.drawio           architecture diagram (+ flow.png export)
docs/                        your PDFs           (gitignored)
evaluations/                 run output          (gitignored)
stories/                     deferred work
tests/                       plain scripts, no pytest
```

### Artifacts and archiving

Each run keeps the submitted file as `source.pdf` alongside the redacted output,
so it stays reviewable later. Removing a run **moves it to
`evaluations/.trash/`** rather than destroying it — these folders hold redacted
copies of real documents and a verbatim list of every PII value found, which
cost billable API time and cannot be reconstructed.

`docs/`, `evaluations/` and `.env` are gitignored, and `*.pdf` is blocked
repo-wide as a safety net.

## Tests

Plain scripts, no framework. Neither needs Azure credentials to pass.

```powershell
.\.venv\Scripts\python.exe tests\test_env_contract.py
.\.venv\Scripts\python.exe tests\test_security.py
.\.venv\Scripts\python.exe -m ruff check .
```

`test_env_contract.py` asserts that every key `write-env.ps1` writes is a key
the engine reads, and vice versa — these drift silently otherwise, leaving the
tool reporting itself unconfigured right after a successful deploy. It also
covers the category matrix and the preview-only gate.

`test_security.py` is a regression guard for findings that were actually closed:
artifact path containment (including Windows UNC paths), run-id validation,
upload names whose *stem* escapes the run folder, cross-origin and DNS-rebinding
refusal, and the absence of secrets in `summary.json`.

```powershell
.\.venv\Scripts\python.exe tests\discover_categories.py   # needs credentials
```

Re-derives the category support matrix from a live resource. Run it after
adding an api-version.

## Running it safely

The console has **no authentication**, and a run folder contains a redacted copy
of your document plus a `result.json` listing every PII value found, verbatim.

- It binds to `127.0.0.1` by default. **Keep it there.** Do not expose it.
- Cross-origin requests are refused, and the `Host` header is checked so a
  rebound DNS name cannot read your runs.
- `docs/`, `evaluations/` and `.env` are gitignored, and `*.pdf` is blocked
  repo-wide as a safety net.
- Deleting a run moves it to `evaluations/.trash/` rather than destroying it.
- Staged source blobs are deleted after each run; if a delete fails the run says
  so rather than leaving a copy in storage silently.
