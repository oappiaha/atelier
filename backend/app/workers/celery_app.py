"""Celery app. The worker is the ONLY thing that talks to model providers
(TDD §1.2). Wada tasks land here in M5-M7."""

from celery import Celery

from app.config import get_settings

celery_app = Celery(
    "atelier",
    broker=get_settings().redis_url,
    backend=get_settings().redis_url,
    include=[
        "app.workers.thumbs",
        "app.workers.segment",
        "app.workers.cutout",
        "app.workers.generate",
    ],
)
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1
# model-calling tasks live on their own queue: a thumbs-only worker must never
# pick up (and fail) work that spends money, and vice versa
celery_app.conf.task_routes = {"wada.*": {"queue": "wada"}}


@celery_app.task
def ping() -> str:
    return "pong"
