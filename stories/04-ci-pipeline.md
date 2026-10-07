# 4 — CI pipeline

**Status:** not started, deliberately · **Depends on:** nothing

## Story

Run the offline tests and a committed-document guard on every push.

## Why

Explicitly out of scope right now — the ask was for something that works
locally, without a pipeline. This is written down so it is a decision rather
than an omission.

The guard matters more than the tests. A PII tool whose repository accidentally
contains a PII-bearing PDF has failed at the only thing it claims to do, and
`.gitignore` alone does not catch a file added with `git add -f` or staged
before the ignore rule existed.

## Acceptance

- [ ] A workflow running on push and pull request.
- [ ] Python 3.11, `pip install -r requirements.txt`.
- [ ] Runs `tests/test_env_contract.py`, which needs no credentials.
- [ ] **Fails the build if any `.pdf` is tracked.**
- [ ] **Fails the build if anything under `docs/` or `evaluations/` other than
      `docs/README.md` is tracked.**
- [ ] A new test file must be added to the workflow explicitly — and that
      footgun is documented, because a test that is not listed never runs.

## Notes

- The guard, roughly:

  ```bash
  if git ls-files | grep -iE '\.pdf$'; then
    echo "::error::Documents must never be committed"; exit 1
  fi
  if git ls-files docs/ evaluations/ | grep -vE '^docs/README\.md$'; then
    echo "::error::docs/ and evaluations/ hold document-derived data"; exit 1
  fi
  ```

- Only `tests/test_env_contract.py` can run in CI. `tests/discover_categories.py`
  needs a live resource and will always have to be run by hand — which is the
  point of it, since it re-derives the category matrix from the service rather
  than from a fixture.
- Tests are plain scripts with a `check(label, actual, expected)` helper and an
  exit code, not pytest. Keep it that way unless there is a reason not to — they
  run identically in CI and by hand.
- There is no linter or formatter config in the project. If one is added, add it
  here at the same time; a style gate that only some people run is worse than
  none.
