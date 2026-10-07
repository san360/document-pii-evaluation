# Backlog

Work that is deliberately **not** built. The scope is a tool that evaluates the
Document PII API from your own machine; everything here goes beyond that.

| # | Story | Status |
|---|---|---|
| [1](01-azure-infrastructure.md) | Azure infrastructure as code — resources only, no hosting | **done** |
| [8](08-service-necessity.md) | What the API actually does, and the undocumented behaviours | **done** |
| [7](07-evaluation-harness.md) | Precision/recall against a labelled corpus | needs ground truth |
| [3](03-managed-identity.md) | Managed identity end to end, no keys | later |
| [4](04-ci-pipeline.md) | CI: offline tests and a committed-document guard | deferred |

## Deliberately out of scope

**Hosting.** No container, App Service or Container App. Story 1 provisions the
services the tool calls and nothing that runs it.

**Everything that is not the Document PII API.** The repo previously also
contained a Text PII path, Document Intelligence OCR, local PyMuPDF word
mapping and redaction, a size-aware PDF splitter, a synthetic-document
generator and a run library. All of it worked; none of it was needed to
evaluate this one API, so it was removed. It is recoverable from git — commit
`5760eb8` is the last one before the removal.

If a question arises that needs it back — "would OCR plus Text PII find more on
this scan?" is a reasonable one — restore it from that commit rather than
rewriting it.

## The open question

Story 7. Both the measured results and the correction in story 8 keep running
into the same wall: there is no ground truth, so "124 entities on this form" and
"10 on this scan" cannot be called good or bad. The larger figure includes
obvious false positives — form-field fragments like `"02"` and `"2025"` flagged
as `USDriversLicenseNumber`, **67 times on one 4-page document**.

Until a labelled corpus exists, this tool measures *behaviour*, not *accuracy*.
That is worth being honest about.

## Conventions for these stories

- **Story** states the outcome, not the implementation.
- **Why** says what is wrong today without it.
- **Acceptance** is a checklist that can be verified.
- **Notes** carries the findings already made, so whoever picks it up does not
  have to rediscover them.
