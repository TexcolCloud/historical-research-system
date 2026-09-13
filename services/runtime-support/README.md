# Shared runtime adapters

`hrs-runtime` provides HTTP correlation IDs, streaming-safe JSON diagnostics, loopback-only per-process metrics, configured model transport, and a thin verified-object adapter over the official boto3 S3 SDK. Business modules own object references, transactions and release decisions; the runtime package does not own the business database.

`object_storage.S3Objects` writes content-addressed objects, verifies a read-back hash and byte count before acknowledgment, and can rebuild a disposable compute cache. SDK retries handle transport failures. Missing S3 configuration or a failed object check never falls back to local business files. Historical local files are not migrated. Callers must explicitly configure the S3 mode and keep references in their transactional database; cache paths are not durable references.

The unified research platform and retained document extraction use this local package. Install from their locked environments or distribute the runtime wheel alongside them; no public registry package is assumed.

Run tests with the platform environment: `python -m pytest services/runtime-support/tests`. Configuration, operational commands and limitations are documented in [engineering.md](../../docs/engineering.md).
