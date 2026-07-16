"""Celery app. The worker is the ONLY thing that talks to model providers
(TDD §1.2). Wada tasks land here in M5-M7."""

from celery import Celery

from app.config import get_settings

celery_app = Celery(
    "atelier",
    broker=get_settings().redis_url,
    backend=get_settings().redis_url,
    include=["app.workers.thumbs"],
)
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1


@celery_app.task
def ping() -> str:
    return "pong"
