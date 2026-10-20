"""Export the canonical DataPipette OpenAPI document."""

import json
from pathlib import Path

from datapipette.config import Settings
from datapipette.main import create_app


def main() -> None:
    """Write a deterministic development contract for review and drift checks."""

    repository_root = Path(__file__).resolve().parents[1]
    output_path = repository_root / "docs" / "openapi.json"
    application = create_app(Settings(environment="test", docs_enabled=True))
    document = application.openapi()
    output_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
