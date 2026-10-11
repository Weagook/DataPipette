# ADR-0022: Shared S3-compatible analytical object storage

- Status: Accepted
- Date: 2026-10-11

## Context

Local Parquet snapshots and exports proved the execution lifecycle but bind readers and workers to one filesystem. Horizontally scaled API and worker processes need one shared private object store. Publication must not activate partial data, downloads must remain authorization-controlled, and existing local objects must stay readable after the default backend changes.

DuckDB can access S3 through optional extensions, but runtime extension installation widens the network and supply-chain boundary. Direct remote scans also need concurrency and range-request benchmarks before becoming the production read path. A correctness-first adapter is needed now without weakening existing query limits.

## Decision

- Persist `storage_backend` (`local` or `s3`) with every snapshot and completed export. Portable generated object keys remain independent of buckets, endpoints, hosts, credentials, and user filenames.
- Keep local filesystem storage as the development/test adapter. Require S3-compatible storage in the production configuration.
- Use boto3, the Apache-2.0 AWS SDK, for SigV4 signing, S3-compatible endpoints, bounded-retry transfers, multipart upload, `HEAD` verification, and presigned GET URLs. The standard library does not provide these protocols.
- Finish and validate each Parquet or export candidate on ephemeral local disk. Compute its byte size and SHA-256, upload under a UUID-derived immutable key, store the checksum as private object metadata, and verify remote size and metadata before metadata PostgreSQL may publish success.
- For the first S3 query path, download the immutable snapshot to a unique ephemeral file, verify its persisted byte size and SHA-256, execute the existing bounded DuckDB query, and always remove the temporary file. Export workers use the same materialization boundary.
- Keep DatePipette's encrypted five-minute capability URL as the authorization boundary. Local objects return a `FileResponse`; S3 objects are verified with `HEAD` and return a `307` redirect to an equally short presigned private-object URL with attachment metadata.
- Support path-style addressing and an optional `AES256` server-side-encryption request. Custom production endpoints must use HTTPS. Static credentials are secret settings; broader IAM/STS credential providers and customer-managed KMS are deferred.
- Add MinIO-compatible local infrastructure and a DatePipette bucket-initialization CLI. Because MinIO stopped publishing anonymously pullable official images in 2026, Compose pins a reproducible community build of the official source release by OCI digest. It is a development/CI dependency, not a linked application library or prescribed production provider.
- Bucket initialization is idempotent and private by default. Production bucket creation, lifecycle rules, versioning, replication, backups, and credentials remain deployment responsibilities.

## Consequences

- API and worker nodes share authoritative snapshots and exports without shared host volumes.
- A completed upload followed by metadata failure may leave an unreferenced object; later garbage collection remains required. It cannot replace the active snapshot.
- Configuration changes do not reinterpret old objects because the backend is durable per artifact. Credentials for every still-referenced backend must remain available.
- Interactive S3 reads currently download a whole Parquet object per request. This is safe and simple but can add latency, bandwidth, and temporary-disk pressure; direct DuckDB S3 reads or a verified cache require a benchmark and follow-up ADR.
- Presigned URLs expose no credentials but remain bearer capabilities until their short expiry. Object storage stays private and DatePipette checks authorization before issuing its capability.
- Local temporary disk is operational workspace, not authoritative data, and can be discarded after each operation.

## Alternatives considered

- **Shared filesystem/NFS:** rejected because S3-compatible storage is the approved production boundary and avoids filesystem coordination semantics.
- **Store snapshots in metadata PostgreSQL:** rejected because analytical data must remain separate from transactional metadata.
- **Install DuckDB `httpfs` dynamically:** rejected because runtime installation adds uncontrolled network and extension lifecycle behavior.
- **Return permanent or public object URLs:** rejected because exports and snapshots contain private business data.
- **Persist bucket and endpoint in every object row:** rejected because they are deployment configuration, while backend and relative key are the portable artifact identity.
- **Use the removed official MinIO images:** rejected because new installations and CI can no longer pull them anonymously.
