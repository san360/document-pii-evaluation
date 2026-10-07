# 8 — What each service is actually needed for

**Status: ANSWERED, then CORRECTED.** Measured against live resources in
`swedencentral` on 2026-10-06, re-tested 2026-10-07. The original measurements
used api-version `2024-11-15-preview`, which has since been **retired from this
tool**; figures re-measured on `2026-05-01` are marked as such.

## The correction, first

An earlier version of this document concluded, in bold:

> **Document PII cannot process image-only scanned PDFs.** Three scans out of
> three.

**That was wrong.** Document PII handles scanned PDFs fine.

### What actually happened

On 2026-10-06 between 18:19 and 18:21 UTC, three scanned PDFs were each
submitted and each failed the same way: job accepted (202), polled ~75 s, then

```json
{"code": "InternalServerError",
 "message": "Failed to process task after several retry",
 "target": "#/tasks/items/0"}
```

A searchable PDF submitted in the same session succeeded. Three-for-three,
with a clean control, against a documented ambiguity about whether scans are
supported at all — so the conclusion looked well-evidenced.

It was not. **All three failures fell inside a two-minute window.** That is one
transient service fault sampled three times, not three independent results. The
control succeeded because it ran at a different moment, not because it was
searchable.

Re-tested the next day, same resources, same documents (on
`2024-11-15-preview`, the version in use at the time):

| Document | Input | Result | Entities |
|---|---|---|---|
| `insurance-application.pdf` | searchable | ✅ succeeded, 19.0 s | 123 |
| `clinical-notes-it.pdf` | **scanned** | ✅ succeeded, 13.7 s | 21 |
| `medical-report-a.pdf` | **scanned** | ✅ succeeded, 13.3 s | 6 |
| `medical-report-b.pdf` | **scanned** | ✅ succeeded, 12.9 s | 3 |

Re-measured on `2026-05-01`, the version the tool now uses — same conclusion,
higher counts:

| Document | Input | Result | Entities |
|---|---|---|---|
| `insurance-application.pdf` | searchable | ✅ succeeded, 29.9 s | 124 |
| `clinical-notes-it.pdf` | **scanned** | ✅ succeeded, 24.5 s | 19 |
| `medical-report-a.pdf` | **scanned** | ✅ succeeded, 18.5 s | 7 |
| `medical-report-b.pdf` | **scanned** | ✅ succeeded, 19.8 s | **10** (was 3) |

Then, suspecting a request-shape difference between the CLI and the web app,
all seven candidate parameter combinations were submitted against the same
scanned document — including the exact block the web app had sent:

```
1 minimal                          OK (11s)
2 + stringIndexType=UnicodeCodePoint   OK (11s)
3 + modelVersion=latest            OK (17s)
4 + domain=none                    OK (11s)
5 all three (what the app sent)    OK (11s)
6 stringIndexType=Utf16CodeUnit    OK (11s)
7 stringIndexType=TextElements_v8  OK (12s)
```

No parameter explains it. The web-app path was then re-run unchanged on the same
scan and also succeeded, returning the same 3 entities as the CLI. There was
never a code difference.

### What to take from it

A clean 3/3 with a working control is still not evidence of a capability limit
when every sample lands in the same two-minute window. The failure mode to guard
against is **correlated sampling**: re-test after a gap before concluding that a
service cannot do something, and prefer `InternalServerError` as evidence of a
bad minute over evidence of a hard boundary — the service says "after several
retry", which is a transient-fault message, not a rejection.

The three findings below were each isolated by bisection against varied inputs
rather than by repeated identical calls, and all three still hold.

---

## Finding 1 — the two APIs do not accept the same category taxonomy

Document PII rejects the **entire request** over one unsupported category — so
on a split document it fails every chunk. Text PII accepts everything. Probed by
[`tests/discover_categories.py`](../tests/discover_categories.py):

| API | api-version | Accepted |
|---|---|---|
| Text PII | 2024-11-01 / 2026-05-01 / 2026-05-15-preview | **177 / 177** |
| Document PII | 2024-11-15-preview *(retired, see below)* | 158 / 177 |
| Document PII | 2026-05-01 / 2026-05-15-preview | 174 / 177 |

> **`2024-11-15-preview` was removed from the tool on 2026-10-07.** Besides
> accepting 16 fewer categories, it detects substantially less: on the same
> scanned page it returned **3 entities where `2026-05-01` returns 10**. The
> table is kept because it explains the `DateOfBirth` rejection that originally
> surfaced this whole line of investigation. `data/entity_categories.json` now
> carries only the two 2026 versions.

`2024-11-15-preview` rejected 19: `Airport`, `BankAccountNumber`,
`CASocialIdentificationNumber`, `CVV`, `City`, `DateOfBirth`,
`DriversLicenseNumber`, `ExpirationDate`, `GPE`, `KRDriversLicenseNumber`,
`KRPassportNumber`, `KRSocialSecurityNumber`, `LicensePlate`, `Location`,
`PassportNumber`, `Password`, `SortCode`, `State`, `VIN`.

The 2026 versions reject only `CASocialIdentificationNumber`,
`KRSocialSecurityNumber` and `VIN` — which **no** Document PII version accepts,
despite all three being valid for Text PII.

Two error formats exist and both must be handled: Text PII names one category
(`Invalid value for parameter 'piiCategories': 'X'`), Document PII names several
(`'X,Y,Z' is not a valid value to be used in 'piiCategories'`).

Also found: **`PersonType` was missing from the taxonomy entirely.** It is absent
from the REST enum in the docs, so the scrape missed it, yet the service accepts
it as a filter *and* returns it in results. The taxonomy is 177, not 176.

**In code:** `filter_categories()` in `evaluate_document_pii.py`; the Evaluate
tab's category picker strikes them out and excludes them before you run; the CLI
passes `--categories` straight through to the same function.

## Finding 2 — the SAS must be valid for at least 24 hours

A shorter-lived SAS is rejected at submit time with:

```
400 InvalidRequest: Invalid analysisInput in request.
    | The request body is invalid or malformed.
```

which names neither the SAS nor the expiry, and is identical to the error for a
genuinely malformed body. Measured by bisection:

| SAS TTL | Result |
|---|---|
| 1h, 23h | rejected |
| **24h** | **accepted** |
| 24.5h, 25h, 26h, 30h, 36h, 48h, 7d | accepted |

An `analyze-documents` job carries its own 24-hour `expirationDateTime`, so the
credentials have to outlive the job. The boundary is exactly 24 hours.

Isolated by noticing that a *fake* storage account with a syntactically valid SAS
is accepted while the real account is rejected — proving the service resolves the
account and validates the token rather than failing on the body.

**In code:** `SAS_LIFETIME`, two days, in `evaluate_document_pii.py`.

## Finding 3 — the `*.result.json` schema is not what the docs imply

Captured from a live run:

```json
{
  "id": "1",
  "statistics": { "charactersCount": 7172, "transactionsCount": 8 },
  "entities": [
    {
      "text": "Contoso Insurance Ltd",
      "type": "Organization",
      "entityId": "1",
      "mask": "**************************",
      "length": 21,
      "confidenceScore": 0.96,
      "tags": [{ "name": "Organization", "confidenceScore": 0.96 }]
    }
  ]
}
```

* the category field is **`type`**, not `category` — reading `category` yields
  `"Unknown"` for every entity, which is exactly what happened on the first run;
* there is **no `offset`**, only a `length`, so an entity cannot be mapped back
  to a position. Anything that needs coordinates must search for the value;
* `text` is **normalised, not verbatim** — a date printed `08/15/2025` comes back
  as `2025-08-15`, so a literal search will not find it.

**In code:** `summarise_entities()` and `harvest()` in
`evaluate_document_pii.py`.

---

## Detection quality

These figures are **historical**, from when the repo still ran both APIs. They
are kept because they are the evidence behind the decision to drop Text PII, and
because they frame story 7. On the searchable insurance form:

| | Document PII | Text PII |
|---|---|---|
| Entities | 123 | 173 |
| Duration | 19.0 s | 2.0 s |

On scans, Document PII was markedly more conservative — 3, 6 and 21 entities
against Text PII's 14, 20 and 18 on the same files. Text PII saw the full OCR
text; Document PII does its own extraction and reported less from it. Which is
*better* is unanswerable without ground truth — that is story 7.

Part of that gap was the api-version, not the API. On `2026-05-01` the same
three scans return 10, 7 and 19 — the 3 became 10. The comparison above would
need re-running to mean anything today, and the Text PII path no longer exists
to run it against.

Neither was clean: both flagged form-field fragments (`"02"`, `"20"`, `"2025"`)
as `USDriversLicenseNumber`, 68 and 71 times respectively on one 4-page form.
On `2026-05-01` that is 67. See story 7.

## Finding 4 — categories are a real filter

Measured on `2026-05-01`, one scanned page, varying only `piiCategories`:

| Requested | Entities returned |
|---|---|
| *(omitted — everything)* | 10 |
| `PhoneNumber` | 1 |
| `Person`, `PhoneNumber` | 2 |
| `Person`, `Organization` | 7 |

So the parameter genuinely narrows the work, rather than filtering a fixed
result set for display. One wrinkle: asking for `Organization` also returns
`OrganizationSports`, so a requested category can bring a sibling with it.

## Finding 5 — batch is faster than per-document, and just as safe

A scanned document does **not** poison a batch submission; all four documents
succeed in one job with identical entity counts to running them separately.
Batch took **58.7 s** against **92.7 s** submitted individually, so
`per-document` buys failure isolation at roughly 1.6× the wall-clock time.

## Finding 6 — preview-only options, and what they cannot fix

`2026-05-15-preview` is a strict superset of GA (223-line spec diff, entirely
additive). Two are implemented: `syntheticReplacement` and `entitySynonyms`.

`syntheticReplacement` works well and `preserveDataFormat` is real — measured
`an 8-digit phone number` → `1-383-696-1596 x8200`, `a date of birth` → `1959-11-27`. Replacements
are stable per value within a run.

**The Learn feature table has an error.** It lists *"Entity label masking
policy"* as preview-only; `entityMask` is in the GA `RedactionPolicyKind` enum
and works on `2026-05-01`. The other six rows match the spec. Also already in
GA despite looking preview-ish: `excludePiiCategories`, `redactionCharacter`
(a closed set of 14 symbols), and per-policy `entityTypes`/`policyName`/
`isDefault`.

### The name that could not be redacted

A filled insurance form appeared to prove Document PII cannot mask names. It
does not:

| Value | Where | Detected |
|---|---|---|
| `Margaret Thompson` | body text | 1.00 |
| `Margaret Thompson` | **table cell** | 1.00 |
| `a borderline name` | body text | 0.94 |
| `a borderline name` | **table cell** | not found |
| `a placeholder name` | anywhere | not found |

Four hypotheses were tested and discarded before the real one:

1. **AcroForm field values** — the file is named `..._Editable.pdf`, so this
   looked certain. `is_form_pdf` is `False` with zero widgets; all text is in
   the content stream.
2. **De-duplication** — the service returned one entity for a name appearing
   twice. A control with the same name three times in body text returned
   **three** entities, all masked. It does not de-duplicate.
3. **Tables** — disproved by the table rows above, and by the fact that `Date`
   in the *same cells* was redacted.
4. **api-version / policy / synonyms / threshold** — preview, `markerMask`,
   `entitySynonyms` and a 0.3 confidence floor all produced identical results.

The answer is that `PlaceholderFirst` is not name-like, and `a borderline name`
is borderline — 0.94 against the 1.00 a real name scores — so it survives in
prose and fails in the terser context of a table cell. This matches the
transparency note: *"Person names in particular require linguistic context."*

**Consequence for story 7:** a corpus built with placeholder names would
measure the model's tolerance for nonsense rather than its accuracy. Ground
truth has to use realistic names.

## Follow-ups

- The `USDriversLicenseNumber` false-positive rate → story 7. This is the one
  that actually matters, and nothing here can settle it without ground truth.
- Whether `2026-05-15-preview` differs from `2026-05-01` in detection, not just
  in accepted categories. Both are offered in the UI; nobody has compared them.
- Whether the Italian document (`clinical-notes-it.pdf`) benefits from
  `--language it`. Tried once: identical results to `en-US`, which is either
  good language detection or the parameter being ignored. Not distinguished.
