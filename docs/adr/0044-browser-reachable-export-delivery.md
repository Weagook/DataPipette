# ADR-0044: Browser-reachable export delivery

- Status: Accepted
- Date: 2026-10-17

## Context

Export capabilities originally redirected every S3-compatible artifact to a presigned object URL. That is efficient when the object endpoint is reachable by the user's browser, but the self-contained evaluation stack intentionally keeps MinIO private and configures the API with the Docker-only hostname `minio`. Returning that hostname to a browser makes an otherwise valid export impossible to download. Publishing MinIO solely to repair this flow would widen the evaluation deployment's network and security boundary.

## Decision

- Keep `redirect` as the default S3 download delivery mode for externally reachable HTTPS object storage.
- Add an explicit `proxy` mode for S3-compatible storage private to the application network. The API downloads the immutable object to a unique temporary path, verifies its persisted byte size and SHA-256, returns it as an attachment, and removes the temporary file after the response completes.
- Configure the bundled Compose/MinIO topology to use proxy mode. MinIO remains loopback-only and the browser receives downloads from the same public application origin.
- Keep the encrypted five-minute DataPipette capability as the only public download URL in both modes. Do not expose storage credentials, internal endpoints, object keys, or absolute paths through the API.

## Consequences

- Evaluation downloads work without publishing MinIO or relying on Docker-internal DNS in a browser.
- External S3 deployments retain direct object delivery and avoid consuming API bandwidth for large exports.
- Proxy mode consumes API bandwidth and temporary disk for the lifetime of a response; operators should size both for the configured export limits.
- A terminated response may delay framework cleanup until process recovery, but temporary files remain non-authoritative and isolated under the configured storage workspace.

## Alternatives considered

- **Publish the embedded MinIO API:** rejected because it expands the public attack surface and still requires externally correct signing host/TLS configuration.
- **Always proxy every S3 download:** rejected because it makes the API an unnecessary data-transfer bottleneck for production object storage.
- **Rewrite presigned URLs in Nginx:** rejected because SigV4 host/path semantics and provider differences make proxy rewriting fragile.
