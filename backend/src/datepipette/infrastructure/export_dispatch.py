"""Redis/Celery export dispatcher."""

from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]


class CeleryExportDispatcher:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def dispatch(self, export_id: UUID) -> None:
        value = str(export_id)
        self._app.send_task(
            "datepipette.export.execute",
            args=[value],
            task_id=f"export-{value}",
            queue="export",
            ignore_result=True,
        )
