"""Celery application with a deliberately small, JSON-only contract."""

from celery import Celery  # type: ignore[import-untyped]

from datapipette.config import Settings, get_settings


def create_celery_app(settings: Settings) -> Celery:
    app = Celery(
        "datapipette",
        broker=settings.redis_url.get_secret_value(),
        include=["datapipette.workers.refresh", "datapipette.workers.exports"],
    )
    app.conf.update(
        accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_default_queue="refresh",
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery_app(get_settings())
