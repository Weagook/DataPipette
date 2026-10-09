"""Redis/Celery refresh dispatcher."""

from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]


class CeleryRefreshDispatcher:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def dispatch(self, run_id: UUID) -> None:
        value = str(run_id)
        self._app.send_task(
            "datepipette.refresh.execute",
            args=[value],
            task_id=value,
            queue="refresh",
            ignore_result=True,
        )
