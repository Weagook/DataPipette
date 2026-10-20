"""HTTP delivery behavior for capability-based export downloads."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from datapipette.api.routes.exports import router
from datapipette.domain.exports import ExportDownload


class TemporaryDownloadService:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def resolve(self, token: str) -> ExportDownload:
        assert token == "valid-capability-token"
        return ExportDownload(
            str(self._path),
            "text/csv",
            "report.csv",
            remove_after_send=True,
        )


def test_proxied_export_is_an_attachment_and_removes_temporary_file(tmp_path: Path) -> None:
    artifact = tmp_path / "temporary-export.csv"
    artifact.write_bytes(b"id\n1\n")
    app = FastAPI()
    app.state.export_download_service = TemporaryDownloadService(artifact)
    app.include_router(router, prefix="/api/v1")

    with TestClient(app) as client:
        response = client.get("/api/v1/export-downloads?token=valid-capability-token")

    assert response.status_code == 200
    assert response.content == b"id\n1\n"
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "report.csv" in response.headers["content-disposition"]
    assert not artifact.exists()
