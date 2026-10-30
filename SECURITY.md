# Security policy

## Supported versions

DataPipette publishes early 0.x releases ([CHANGELOG.md](CHANGELOG.md)). Security fixes are made on `main` and released in the next patch of the latest minor version.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| Earlier, unreleased revisions | No |

Before 1.0, upgrade to the latest patch release to receive fixes. The single-server installation, its limits, and the upgrade procedure are described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Reporting a vulnerability

Do not disclose a suspected vulnerability in a public issue, discussion, pull request, or chat transcript.

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Only the maintainers can see the report. If that option is unavailable, contact the repository owner privately and share only enough to establish a secure channel.

Include, when safe:

- the affected component and version or commit;
- prerequisites and impact;
- minimal reproduction steps;
- whether credentials or real data may have been exposed; and
- a suggested mitigation, if known.

Do not access data that does not belong to you, degrade a system, or retain secrets while investigating.

You can expect an acknowledgement within seven days. Confirmed issues are fixed in a patch release, whose changelog credits the reporter unless they prefer otherwise.

## Security architecture

Trust boundaries and controls, including which are implemented and which remain planned, are documented in [docs/SECURITY.md](docs/SECURITY.md). The most recent review and its accepted risks are in [docs/SECURITY_REVIEW_2026-10-29.md](docs/SECURITY_REVIEW_2026-10-29.md).
