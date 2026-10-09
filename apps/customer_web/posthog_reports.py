"""Durable, coalesced PostHog query jobs. HTTP never waits for the provider."""
import hashlib
import json
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import JourneyReport
from .posthog_services import project_config


def visitor_snapshot(branch, filters, force_refresh=False):
    config = project_config(branch.pk)
    if not config['enabled']:
        return {'available': False, 'message': 'PostHog is not configured for this outlet.'}
    if not settings.POSTHOG_QUERY_API_KEY:
        return {'available': False, 'message': 'Visitor reporting needs a read-only PostHog query key on the backend.'}
    normalized = {key: str(value) for key, value in filters.items() if key in ('start_date', 'end_date', 'start_time', 'end_time')}
    key = hashlib.sha256(json.dumps(['posthog-visitors-v1', branch.pk, config['project_url'], normalized], sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        report, _ = JourneyReport.objects.get_or_create(key=key, defaults={
            'branch': branch, 'filters': {**normalized, '_posthog': True}})
        report = JourneyReport.objects.select_for_update().get(pk=report.pk)
        stale = not report.completed_at or report.completed_at < timezone.now()-timedelta(seconds=60)
        if report.status in ('READY', 'FAILED') and (force_refresh or stale):
            report.status = 'PENDING'; report.attempts = 0; report.save(update_fields=['status', 'attempts'])
    pending = report.status in ('PENDING', 'RUNNING')
    result = dict(report.result) if report.result.get('available') else {'available': False}
    result.update({'refreshing': pending, 'stale': pending or report.status == 'FAILED',
        'message': 'Updating visitor analytics…' if pending else 'Visitor refresh failed. Showing the last successful result.' if report.status == 'FAILED' else ''})
    return result
