import re
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from apps.user_accounts.models import User
from apps.user_accounts.serializers import UserOutputSerializer
from .models import CustomerProfile, SignupChallenge


def phone_number(value):
    digits = re.sub(r'\D', '', str(value))
    if len(digits) == 10 and digits.startswith(('97', '98')):
        digits = '977' + digits
    if not re.fullmatch(r'9779[78]\d{8}', digits):
        raise ValidationError({'phone': 'Enter a valid Nepal mobile number.'})
    return '+' + digits


class AuthThrottle(SimpleRateThrottle):
    scope = 'customer_auth'
    rate = '10/min'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class PhoneThrottle(AuthThrottle):
    scope = 'customer_phone'

    def get_cache_key(self, request, view):
        phone = re.sub(r'\D', '', str(request.data.get('phone', '')))[-10:]
        return self.cache_format % {'scope': self.scope, 'ident': phone or self.get_ident(request)}


def session_data(user):
    refresh = RefreshToken.for_user(user)
    refresh.set_exp(lifetime=timedelta(days=30))
    return {'access': str(refresh.access_token), 'refresh': str(refresh), 'user': UserOutputSerializer(user).data, 'outlet': None}


class CustomerAuthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthThrottle, PhoneThrottle]

    def post(self, request, action):
        data = request.data
        from . import otp
        if action in ('start', 'recovery-start'):
            phone = phone_number(data.get('phone'))
            exists = User.objects.filter(phone_number__in=[phone, phone[4:]]).exists()
            if action == 'start' and exists:
                return Response({'exists': True})
            if action == 'recovery-start' and not User.objects.filter(phone_number__in=[phone, phone[4:]], role='CUSTOMER', is_active=True).exists():
                raise ValidationError('No active customer account found. Please sign up first.')
            result = otp.issue(phone, 'SIGNUP' if action == 'start' else 'RECOVERY', data.get('outlet_id'))
            return Response({'exists': exists, **result})
        if action in ('verify', 'recovery-verify'):
            recovery = action == 'recovery-verify'
            token = otp.verify(data, 'RECOVERY' if recovery else 'SIGNUP')
            return Response({'reset_token' if recovery else 'registration_token': token})
        if action == 'reset':
            return Response(session_data(otp.reset_credentials(data)))
        if action == 'sms-status':
            from .models import SmsDelivery
            row = SmsDelivery.objects.filter(challenge_id=serializers.UUIDField().run_validation(data.get('challenge_id'))).first()
            if not row:
                raise ValidationError('Verification request not found.')
            return Response({'status': row.status})
        if action == 'register':
            serializer = RegistrationInput(data=data)
            serializer.is_valid(raise_exception=True)
            values = serializer.validated_data
            try:
                challenge_id = signing.loads(values['registration_token'], salt='customer-register', max_age=300)
            except signing.BadSignature:
                raise ValidationError('Signup verification expired.')
            try:
                with transaction.atomic():
                    challenge = SignupChallenge.objects.select_for_update().filter(pk=challenge_id, purpose='SIGNUP', verified=True, consumed=False, expires_at__gt=timezone.now()).first()
                    if not challenge:
                        raise ValidationError('Request a new signup code.')
                    user = User(username=values['username'], email=values.get('email', '').lower() or None, phone_number=challenge.phone, role='CUSTOMER')
                    try:
                        validate_password(values['password'], user)
                    except DjangoValidationError as error:
                        raise ValidationError({'password': error.messages})
                    user.set_password(values['password'])
                    user.save()
                    CustomerProfile.objects.create(user=user, pin_hash=make_password(values['pin']))
                    challenge.consumed = True
                    challenge.save(update_fields=['consumed'])
            except IntegrityError:
                raise ValidationError('This mobile, username, or email is already registered.')
            return Response(session_data(user), status=201)
        if action == 'login':
            phone = phone_number(data.get('phone'))
            user = User.objects.filter(phone_number__in=[phone, phone[4:]], role='CUSTOMER', is_active=True).first()
            credential = str(data.get('credential', ''))
            valid = False
            if user and data.get('method') == 'PIN' and re.fullmatch(r'\d{4}', credential):
                with transaction.atomic():
                    profile = CustomerProfile.objects.select_for_update().filter(user=user).first()
                    if profile and (not profile.pin_locked_until or profile.pin_locked_until <= timezone.now()):
                        valid = check_password(credential, profile.pin_hash)
                        if valid:
                            profile.pin_failures = 0
                            profile.pin_locked_until = None
                        else:
                            profile.pin_failures += 1
                            if profile.pin_failures >= 5:
                                profile.pin_locked_until = timezone.now()+timedelta(minutes=15)
                                profile.pin_failures = 0
                        profile.save(update_fields=['pin_failures','pin_locked_until'])
            elif user and data.get('method') == 'PASSWORD':
                valid = user.check_password(credential)
            if not valid:
                raise PermissionDenied('Mobile number or credentials are incorrect.')
            return Response(session_data(user))
        raise ValidationError('Unknown authentication action.')


class RegistrationInput(serializers.Serializer):
    registration_token = serializers.CharField()
    username = serializers.RegexField(r'^[\w.@+-]+$', min_length=3, max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    pin = serializers.RegexField(r'^\d{4}$')
    password = serializers.CharField(min_length=8, max_length=128, trim_whitespace=False)
