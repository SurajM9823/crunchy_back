"""Durable SMS queue. Broker messages contain no OTPs or provider credentials."""
import json
from datetime import timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from .models import SmsDelivery
from .secrets import unseal


def dispatch(row):
    org = row.challenge.restaurant
    code = unseal(row.payload_encrypted)
    purpose = 'signup' if row.challenge.purpose == 'SIGNUP' else 'password/PIN reset'
    body = urlencode({'token': unseal(org.sms_token_encrypted), 'from': org.sms_sender,
        'to': row.challenge.phone[-10:],
        'text': f'{org.name}: {code} is your {purpose} code. Expires in 5 minutes. Do not share it.'}).encode()
    request = Request('https://api.sparrowsms.com/v2/sms/', data=body, method='POST')
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(16384))
        return response.status == 200 and str(payload.get('response_code')) == '200', str(payload.get('response_code', 'invalid_response'))[:40]


@shared_task
def send_pending_sms():
    now = timezone.now()
    # Expired payloads are erased, including ones left behind by a crashed worker.
    SmsDelivery.objects.filter(challenge__expires_at__lte=now).exclude(payload_encrypted='').update(status='EXPIRED', payload_encrypted='')
    ids = list(SmsDelivery.objects.filter(status='PENDING', next_attempt_at__lte=now).values_list('pk', flat=True)[:100])
    for pk in ids:
        # The row lock prevents two workers from submitting the same message concurrently.
        with transaction.atomic():
            row = SmsDelivery.objects.select_for_update(of=('self',)).select_related('challenge__restaurant').get(pk=pk)
            if row.status != 'PENDING' or row.next_attempt_at > timezone.now():
                continue
            org = row.challenge.restaurant
            if row.challenge.consumed or row.challenge.verified or row.challenge.expires_at <= timezone.now():
                row.status, row.payload_encrypted = 'EXPIRED', ''
            elif not org or not org.sms_enabled:
                row.status, row.payload_encrypted, row.last_error = 'FAILED', '', 'sms_disabled'
            else:
                row.attempts += 1
                retryable = True
                try:
                    accepted, error = dispatch(row)
                    retryable = False
                except HTTPError as exc:
                    accepted, error_code = False, exc.code
                    retryable = error_code >= 500 or error_code == 429
                    error = f'http_{error_code}'
                except Exception:
                    # Never store/log provider payloads or exceptions containing credentials.
                    accepted, error = False, 'transport_or_configuration_error'
                if accepted:
                    row.status, row.payload_encrypted, row.last_error = 'SENT', '', ''
                elif not retryable or row.attempts >= 4:
                    row.status, row.payload_encrypted, row.last_error = 'FAILED', '', error
                else:
                    row.last_error = error
                    row.next_attempt_at = timezone.now()+timedelta(seconds=60*2**(row.attempts-1))
            row.save()


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def prune_website_visits():
    from .models import WebsiteVisit
    WebsiteVisit.objects.filter(created_at__lt=timezone.now()-timedelta(days=90)).delete()
    from .models import JourneySession, JourneyReport
    JourneySession.objects.filter(last_seen__lt=timezone.now()-timedelta(days=90)).delete()
    JourneyReport.objects.filter(created_at__lt=timezone.now()-timedelta(days=2)).delete()
    from .models import PostHogDelivery, PostHogOrderIdentity
    PostHogDelivery.objects.filter(status='SENT', sent_at__lt=timezone.now()-timedelta(days=90)).delete()
    PostHogOrderIdentity.objects.filter(created_at__lt=timezone.now()-timedelta(days=90)).delete()


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def send_posthog_events():
    """Database outbox with stable event IDs across retry and worker crashes."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from .models import PostHogDelivery
    now = timezone.now()
    PostHogDelivery.objects.filter(status='SENDING', next_attempt_at__lte=now).update(status='PENDING')
    ids = list(PostHogDelivery.objects.filter(status='PENDING', next_attempt_at__lte=now).order_by('created_at').values_list('pk', flat=True)[:50])
    for pk in ids:
        with transaction.atomic():
            row = PostHogDelivery.objects.select_for_update().get(pk=pk)
            if row.status != 'PENDING' or row.next_attempt_at > timezone.now():
                continue
            if row.attempts >= 4:
                row.status = 'FAILED'; row.last_error = 'worker_recovery_limit'; row.save(); continue
            row.status = 'SENDING'; row.attempts += 1
            row.next_attempt_at = timezone.now()+timedelta(minutes=2)
            row.save()
        retryable, error = True, ''
        try:
            if row.api_host not in ('https://us.i.posthog.com', 'https://eu.i.posthog.com'):
                raise ValueError('Invalid analytics host')
            request = Request(row.api_host+'/batch/', data=json.dumps({'api_key': row.project_token, 'batch': [row.payload]}).encode(),
                headers={'Content-Type':'application/json'}, method='POST')
            with urlopen(request, timeout=15) as response:
                if not 200 <= response.status < 300:
                    raise ValueError('Unexpected analytics response')
            row.status = 'SENT'; row.sent_at = timezone.now()
        except HTTPError as exc:
            error = f'http_{exc.code}'; retryable = exc.code == 429 or exc.code >= 500
        except Exception as exc:
            error = type(exc).__name__[:80]
        if error:
            row.last_error = error
            row.status = 'PENDING' if retryable and row.attempts < 4 else 'FAILED'
        row.next_attempt_at = timezone.now()+timedelta(seconds=60*2**max(0,row.attempts-1))
        row.save()


@shared_task(autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def build_journey_reports():
    """Durable pending rows survive broker failure; short claim locks prevent duplicate work."""
    from django.core.serializers.json import DjangoJSONEncoder
    from .models import JourneyReport
    from .journey_selectors import ReportFilters, build_report
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from django.db.models import Q
    JourneyReport.objects.filter(status='RUNNING',completed_at__lt=timezone.now()-timedelta(minutes=10)).update(status='PENDING')
    due=Q(attempts=0)
    for attempt in range(1,4):
        due |= Q(attempts=attempt,completed_at__lte=timezone.now()-timedelta(seconds=60*2**(attempt-1)))
    for pk in JourneyReport.objects.filter(due,status='PENDING').order_by('created_at').values_list('pk',flat=True)[:5]:
        with transaction.atomic():
            report=JourneyReport.objects.select_for_update().select_related('branch').get(pk=pk)
            if report.status!='PENDING': continue
            report.status='RUNNING';report.attempts+=1;report.completed_at=timezone.now();report.save()
        try:
            if report.filters.get('_posthog'):
                from .posthog_selectors import visitor_overview
                from .posthog_services import project_config
                from .journey_selectors import bounds
                serializer=ReportFilters(data=report.filters);serializer.is_valid(raise_exception=True)
                start,end=bounds(serializer.validated_data)
                result=visitor_overview(report.branch,project_config(report.branch_id),start,end,force_refresh=True)
                if not result.get('available'):
                    raise ValueError('PostHog query unavailable')
            elif '_question' in report.filters:
                from .journey_diagnostics import analyst_answer
                source=JourneyReport.objects.get(pk=report.filters['_report_id'],branch=report.branch,status='READY')
                result=analyst_answer(source.result,report.filters['_question'],use_ai=True)
            else:
                serializer=ReportFilters(data=report.filters);serializer.is_valid(raise_exception=True)
                result=build_report(report.branch,serializer.validated_data)
            report.result=json.loads(json.dumps(result,cls=DjangoJSONEncoder));report.status='READY'
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning('Analytics report %s failed (%s), attempt %s', report.pk, type(exc).__name__, report.attempts)
            report.status='FAILED' if report.attempts>=3 else 'PENDING'
        report.completed_at=timezone.now();report.save()
        try:
            async_to_sync(get_channel_layer().group_send)(f'customer_accounts_{report.branch_id}',{
                'type':'account_event','event_type':'ANALYTICS_READY','event_id':f'{report.pk}:{report.completed_at.isoformat()}','outlet_id':report.branch_id,
                'timestamp':report.completed_at.isoformat()})
        except Exception:
            pass  # Existing WebSocket heartbeat compares durable report revision after recovery.
