# ADR-0055: Versioned releases and published images

- Status: Accepted
- Date: 2026-10-30

## Context

Every installation built its images from the checked-out source on the server. An upgrade meant `git pull` and a rebuild, which takes minutes on a small server, depends on npm and PyPI being reachable at that moment, and leaves no clear answer to "which version is running". There was no changelog, and nothing told an operator which upgrades needed manual steps.

## Decision

- Use Semantic Versioning. Before 1.0, a minor version may break the public API or need manual upgrade steps, and its changelog section says so.
- Keep one version for the whole product in five files: both `pyproject.toml` files, `datapipette.__version__`, the frontend, and the TypeScript client. `tools/check_release_version.py` checks that they agree and that `CHANGELOG.md` has a dated section for that version. CI runs it on every change.
- A pushed `vX.Y.Z` tag triggers the `Release` workflow, which:
  - re-checks the versions;
  - builds both images for `linux/amd64` and `linux/arm64` from the existing Dockerfiles;
  - publishes them to GitHub Container Registry as `X.Y.Z`, `X.Y`, and `latest`, with OCI source, version, and revision labels;
  - creates the GitHub release from the changelog section.

  A manual run builds both platforms without publishing, as a pre-release check.
- Build without third-party Docker actions. The workflow uses `docker buildx` with the built-in `GITHUB_TOKEN` and a digest-pinned binfmt image, keeping the repository's rule of commit-pinned actions without adding new action dependencies.
- Compose reads the image names and tag from `DATAPIPETTE_BACKEND_IMAGE`, `DATAPIPETTE_FRONTEND_IMAGE`, and `DATAPIPETTE_IMAGE_TAG`. The defaults keep building from source. `new-env.sh --release X.Y.Z` writes the published images.
- Upgrades go forward only: back up, check out the new tag, pull, and start (migrations run before the application). Going back means restoring the backup with the older tag.

## Consequences

- Production upgrades take a pull and a restart, need no build tools on the server, and are reproducible: every installation of `0.1.0` runs the same bytes.
- Releasing becomes a deliberate step with a written changelog. A version bump without a changelog section fails CI.
- The repository is public, so its packages are public and servers need no registry credentials. A private fork would need `docker login ghcr.io` with a read-only token.
- Multi-platform builds run under emulation and take longer than a single-platform build. That is acceptable for an occasional release job.

## Alternatives considered

- **Docker Hub:** rejected because GitHub Container Registry sits next to the source, uses the workflow's own token, and links images to the repository.
- **Image tags from commit SHAs only:** rejected because operators need human versions and release notes to decide when to upgrade.
- **The `docker/*` GitHub actions:** rejected to avoid four new third-party action dependencies for what one `docker buildx build` command does.
