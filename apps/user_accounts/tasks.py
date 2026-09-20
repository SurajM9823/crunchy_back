import logging
from celery import shared_task
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_login_notification_task(self, user_id: int, identifier_used: str, ip_address: str = 'unknown'):
    """
    Background Celery task to send security/login notifications.
    Can be dispatched after successful superuser or staff login.
    """
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        logger.info(
            f"[Celery Worker] Login notification for {user.username} (Role: {user.role}) "
            f"via {identifier_used} from IP: {ip_address}"
        )
        return {
            'status': 'sent',
            'user_id': user.id,
            'role': user.role,
            'identifier': identifier_used,
        }
    except User.DoesNotExist:
        logger.warning(f"[Celery Worker] User with id {user_id} not found.")
        return {'status': 'user_not_found'}
    except Exception as exc:
        logger.error(f"[Celery Worker] Error sending notification: {exc}")
        raise self.retry(exc=exc)


@shared_task
def restaurant_audit_heartbeat_task():
    """
    Scheduled heartbeat task for verifying Celery health in Crunchy RMS.
    """
    logger.info("[Celery Heartbeat] Crunchy RMS background worker is healthy.")
    return {'status': 'healthy'}

