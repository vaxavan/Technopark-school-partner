from celery import shared_task

from . import services


@shared_task
def publish_due():
    services.release_stuck()
    done = 0
    for pub_id in services.due_publications():
        if services.publish_one(pub_id) is not None:
            done += 1
    return done + services.release_partner_only_due()


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def capture_publication_evidence(publication_id):
    services.capture_for_publication(publication_id)
