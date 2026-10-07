# docs/

Put the PDFs you want to evaluate here.

**Everything in this folder except this README is gitignored, and it must stay
that way.** A document you bring to a PII tool is, by definition, a document
that contains PII — so it is treated as customer data and never committed.
`*.pdf` is also blocked repo-wide as a second layer.

Nothing in the app requires a file to be in this folder; it is simply a
convenient, already-ignored place to keep test documents so they cannot be
staged by accident. `--input` points anywhere:

```powershell
python evaluate_document_pii.py --input docs
python evaluate_document_pii.py --input C:\somewhere\else
python evaluate_document_pii.py --input C:\somewhere\one-file.pdf
```

## What to put here

Both kinds are worth having, because the interesting question is whether they
behave differently:

- **Searchable** — a PDF with a real text layer.
- **Scanned** — image-only pages, no text layer.

The tool labels each file as one or the other in the results table. That label
is local bookkeeping for your benefit and plays no part in the API call — the
same bytes are sent either way.

Measured on `2026-05-01`: scanned 3/3 and searchable 1/1 succeed. If you hit an
`InternalServerError` on a scan, re-test before concluding anything — this repo
once declared scanned PDFs unsupported on the strength of three failures that
all landed inside the same two-minute window. See
[`stories/08-service-necessity.md`](../stories/08-service-necessity.md).

> There is no synthetic-document generator in this repo. An earlier version
> shipped one, but documents it produced were useless for judging the service:
> the PII in them came from the same templates you would score against. Use
> real documents, or realistic ones you are allowed to keep locally.
