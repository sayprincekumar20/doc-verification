from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("doc_verification", broker=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,              # a job is re-delivered if a worker dies mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,     # long OCR/AI tasks: take one at a time
    task_default_queue="verification",
    broker_connection_retry_on_startup=True,
    timezone="Asia/Manila",
)
