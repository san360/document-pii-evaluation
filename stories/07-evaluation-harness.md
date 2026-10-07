# 7 — Evaluation harness

**Status:** not started · **Depends on:** a labelled corpus that does not exist

## Story

Measure precision and recall for each api-version, category set and document
type, so a configuration change can be judged rather than guessed at.

## Why

The tool reports what the service returned. It cannot say whether that is
*right*. Every tuning decision available — api-version, category selection,
masking policy, language — is currently made on impression.

The numbers make the problem concrete. On a 4-page insurance form the service
returns **124 entities, 67 of them `USDriversLicenseNumber`** — and spot-checking
shows many are form-field fragments like `"02"` and `"2025"`. Is that 67 false
positives, or a form that genuinely repeats a licence number? Nobody can say
without ground truth, and the same question applies to every figure this tool
prints.

## Acceptance

- [ ] A corpus format: document plus a ground-truth file listing each expected
      value, its category and its page.
- [ ] A runner that executes a configuration matrix over the corpus and writes
      one result file per cell.
- [ ] Precision, recall and F1 overall, per category, and per document type
      (searchable / scanned).
- [ ] A comparison view: two configurations side by side, with the documents
      they disagree on listed first.
- [ ] Cost and wall-clock time per configuration, since these trade against
      accuracy.

## Notes

- The corpus must not live in this repository, for the same reason `docs/` is
  gitignored. Point at it with an environment variable, and have every
  corpus-dependent part of the UI hide itself cleanly when it is not configured —
  that is the normal case.
- **Matching will be the hard part, and it is the API's doing.** `result.json`
  carries no `offset` — only a length — so an entity cannot be mapped back to a
  position in the document. Worse, `text` is normalised: a date printed
  `08/15/2025` comes back as `2025-08-15`, so even a literal search against the
  source can miss it. Expect to match on normalised forms and expect that to be
  the main source of disagreement between the harness and reality.
- Because there are no offsets, **per-page localisation cannot be scored at
  all** from the API response. If that matters, it has to come from the redacted
  PDF the service returns, not from the JSON.
- Use real documents. A synthetic generator was deliberately removed from this
  repo: PII generated from the same templates a matcher looks for produces
  scores that mean nothing.
- Confidence threshold sweeps are cheap to add here and are probably the first
  thing anyone will want — `confidenceScore` is already in every entity.
- A single data point to beat: **`2026-05-01` found 10 entities on a scan where
  the retired `2024-11-15-preview` found 3.** More is not automatically better,
  and that is exactly the claim this story exists to settle.
