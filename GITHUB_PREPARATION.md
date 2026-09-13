# GitHub source baseline

This repository contains the application source, dependency locks, database migrations,
API contracts, deployment scripts, CI configuration, and synthetic test fixtures.
The production entry is `scripts/hrs_v2.py`; deployment details are in
`services/research-platform/README.md`.

## Upload scope

Use the `codex/github-baseline` branch. It starts a new history from the reviewed
source snapshot. The previous local `main` history is retained for recovery and
must not be included in this upload: it contains historical files that are now
excluded. Do not use `git push --all` or `git push --mirror` for this baseline.

The following remain local and are not distributed: root `docs/`, the two tuning
archive directories, historical evaluation JSON and generated assets, original
books, models, `.env`, database/S3 state, backups, logs, caches and build outputs.
Evaluation tools remain available, but real-source evaluation requires the separate
local datasets. Links to excluded archives in module documents are local references.

No GitHub repository or remote is configured by this preparation. After creating an
empty repository and replacing the placeholder with its actual URL:

```sh
git remote add origin <GITHUB_REPOSITORY_URL>
git push -u origin codex/github-baseline:main
```

Push only this branch. No credentials belong in the remote URL. No license for the
project itself is granted by this preparation; third-party notices are retained.

## Validation scope

Verified on 2026-09-13:

- Platform/runtime tests: 41 passed, 10 opt-in tests skipped.
- Frontend tests: 22 passed across 12 files.
- Ruff, frontend unused-code checks, OpenAPI generation, and frontend production
  build: passed. One migration import-order issue was corrected.
- API and Web Docker images: built successfully from a Git-exported source tree
  without local environments, original books, credentials or private archives.
- Packaged API/search/cards imports and tokenizer hash/loading: passed in an
  isolated container with networking disabled.
- Candidate files: credential-pattern and local-secret-value checks found no
  matches. This is a bounded check, not a guarantee of absence of every secret.
- Excluded-data checks and required deployment/build resources: passed.

Non-blocking output: the frontend build reports one chunk slightly above 500 kB;
the DOM test environment reports disabled PDF iframe loading. Neither failed the
tests or build. These checks do not constitute PDF browser acceptance.

This is a source-release baseline, not an assertion that real-book acceptance is
finished. Live model/workflow tests requiring explicit opt-in remain separate.
A fresh machine still needs the external PostgreSQL, S3 and OpenSearch services,
local OCR and vision models, GPU environment, and real configuration. Preparation
does not restart the running book task or publish/deploy its changes.
