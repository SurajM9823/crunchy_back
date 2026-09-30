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
