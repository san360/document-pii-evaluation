# design/

How the app actually works, as a diagram.

| File | What it is |
|---|---|
| `flow.drawio` | The source. One page — open it in [draw.io](https://app.diagrams.net), the VS Code **Draw.io Integration** extension, or draw.io Desktop. |
| `flow.png` | The exported diagram, committed so it is readable from GitHub without installing anything. |

There is deliberately **no PDF export committed**. `.gitignore` blocks `*.pdf`
everywhere as a safety net against committing a customer document, and that
invariant is worth more than the convenience of a checked-in PDF. Generate one
locally if you want it — the command is below.

## What the diagram shows

The eight steps a submitted document goes through, from either surface, and
what each one costs.

Colour has meaning:

- **Green** — happens on your machine, no network.
- **Blue** — a call to Azure.
- **Red** — a gotcha found by measurement, not documentation.
- **Amber** — something you remain responsible for afterwards.

Two things are worth noticing.

**Only three of the eight steps touch the network** — the blob upload, the job
itself, and the artifact download. Everything green is bookkeeping: labelling
the input, dropping categories the api-version would reject, and writing the
run out. The service does the extraction, the detection and the redaction; this
tool does not inspect the document at all.

**Both surfaces converge immediately.** The browser and the CLI meet at
`run_evaluation()` before anything happens, which is why they cannot send
different requests. That shared box is the whole reason the diagram has one
flow rather than two.

## What it deliberately does not show

Earlier versions of this repo also ran Text PII, Document Intelligence OCR, a
PyMuPDF word map, offset→rectangle mapping, local redaction and a size-aware
splitter — and the diagram had a second page comparing data residency across
the two API paths. None of that exists now; the only Azure call is
`analyze-documents/jobs`. The old diagram is in the git history if the
comparison ever becomes interesting again.

## Where the data goes

Worth being explicit, since it is the question that matters for a PII tool:
**the document itself is uploaded to blob storage in your own subscription.**
The API cannot accept a file directly — it reads its input from a blob and
writes its output to a container you own.

- `pii-source` — the staged document. **Deleted after each run** unless you
  pass `--keep-uploads`.
- `pii-target` — the redacted file and `result.json`. **Not** cleaned up; your
  own lifecycle policy applies.
- `evaluations/<timestamp>/` — a local copy of both, plus `source.pdf` and
  `summary.json`. Gitignored.

## Re-exporting after an edit

```powershell
$exe = "C:\Program Files (x86)\draw.io\draw.io.exe"
& $exe --export --page-index 1 --format png --scale 2 --border 12 `
       --output design\flow.png design\flow.drawio

# a local PDF, if you want one — do not commit it, see above
& $exe --export --format pdf --crop --output "$env:TEMP\flow.pdf" design\flow.drawio
```

Note `--page-index` is **1-based** in draw.io ≥ 27.0.2 and 0-based before it.
