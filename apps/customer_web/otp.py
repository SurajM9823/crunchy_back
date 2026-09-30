"""Single-use customer verification and recovery challenges."""
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, Throttled
from apps.restaurants.models import Restaurant, Branch
from apps.user_accounts.models import User
from .models import SignupChallenge, SmsDelivery, CustomerProfile
from .secrets import seal


def issue(phone, purpose, outlet_id=None):
    if purpose not in ('SIGNUP', 'RECOVERY'):
        raise ValidationError('Unknown verification purpose.')
    if outlet_id:
        outlet_id = serializers.IntegerField(min_value=1).run_validation(outlet_id)
        branch = Branch.objects.filter(pk=outlet_id, is_active=True, restaurant__is_active=True).first()
        org_id = branch.restaurant_id if branch else None
    else:
        ids = list(Restaurant.objects.filter(is_active=True).values_list('pk', flat=True)[:2])
        org_id = ids[0] if len(ids) == 1 else None
    if not org_id:
        raise ValidationError('Select a valid outlet before requesting a code.')
    with transaction.atomic():
        org = Restaurant.objects.select_for_update().get(pk=org_id)
        live_sms = org.sms_enabled and org.sms_sender and org.sms_token_encrypted
        if not live_sms and not settings.CUSTOMER_DEMO_OTP:
            raise ValidationError('SMS verification is not configured. Please contact the outlet.')
        now = timezone.now()
        recent = SignupChallenge.objects.filter(phone=phone)
        if recent.filter(created_at__gt=now-timedelta(seconds=60)).exists():
            raise Throttled(wait=60, detail='Please wait before requesting another code.')
        if recent.filter(created_at__gt=now-timedelta(hours=1)).count() >= 5 or recent.filter(created_at__gt=now-timedelta(days=1)).count() >= 10:
            raise Throttled(wait=3600, detail='Code request limit reached. Please try later.')
        recent.filter(purpose=purpose, consumed=False).update(consumed=True)
        code = f'{secrets.randbelow(10000):04d}'
        challenge = SignupChallenge.objects.create(phone=phone, purpose=purpose, restaurant=org,
            code_hash=make_password(code), expires_at=now+timedelta(minutes=5))
        if live_sms:
            SmsDelivery.objects.create(challenge=challenge, payload_encrypted=seal(code), next_attempt_at=now)
        result = {'challenge_id': str(challenge.pk), 'expires_in': 300, 'resend_after': 60,
                  'delivery_status': 'QUEUED' if live_sms else 'DEMO'}
        if not live_sms:
            result['demo_code'] = code
        return result


def verify(data, purpose):
    challenge_id = serializers.UUIDField().run_validation(data.get('challenge_id'))
    with transaction.atomic():
        challenge = SignupChallenge.objects.select_for_update().filter(pk=challenge_id, purpose=purpose).first()
        valid = challenge and not challenge.consumed and not challenge.verified and challenge.expires_at > timezone.now() and challenge.attempts < 5
        if valid:
            challenge.attempts += 1
            valid = check_password(str(data.get('code', '')), challenge.code_hash)
            challenge.verified = bool(valid)
            challenge.save(update_fields=['attempts', 'verified'])
    if not valid:
        raise ValidationError('Invalid or expired code. Request a new code.')
    return signing.dumps(str(challenge.pk), salt='customer-register' if purpose == 'SIGNUP' else 'customer-recovery')


def reset_credentials(data):
    values = ResetInput(data=data)
    values.is_valid(raise_exception=True)
    values = values.validated_data
    try:
        challenge_id = signing.loads(values['reset_token'], salt='customer-recovery', max_age=300)
    except signing.BadSignature:
        raise ValidationError('Recovery verification expired. Request a new code.')
    with transaction.atomic():
        challenge = SignupChallenge.objects.select_for_update().filter(pk=challenge_id, purpose='RECOVERY',
            verified=True, consumed=False, expires_at__gt=timezone.now()).first()
        if not challenge:
            raise ValidationError('Recovery verification expired. Request a new code.')
        user = User.objects.select_for_update().filter(phone_number__in=[challenge.phone, challenge.phone[4:]], role='CUSTOMER', is_active=True).first()
        if not user:
            raise ValidationError('Unable to recover this account.')
        try:
            validate_password(values['password'], user)
        except DjangoValidationError as error:
            raise ValidationError({'password': error.messages})
        user.set_password(values['password'])
        user.save(update_fields=['password'])
        CustomerProfile.objects.update_or_create(user=user, defaults={'pin_hash': make_password(values['pin']), 'pin_failures': 0, 'pin_locked_until': None})
        challenge.consumed = True
        challenge.save(update_fields=['consumed'])
        return user


class ResetInput(serializers.Serializer):
    reset_token = serializers.CharField()
    password = serializers.CharField(min_length=8, max_length=128, trim_whitespace=False)
    pin = serializers.RegexField(r'^\d{4}$')
