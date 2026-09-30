import re
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from apps.restaurants.models import Branch
from apps.user_accounts.models import User
from .models import CustomerContact, WebsiteVisit


def normalized_phone(value):
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 10:
        digits = '977'+digits
    return '+'+digits if re.fullmatch(r'9779[78]\d{8}', digits) else None


@transaction.atomic
def remember_contact(branch_id, phone, name, source, user=None, seen=None, login=False):
    phone = normalized_phone(phone)
    if not phone:
        return
    if user is None:
        user = User.objects.filter(role='CUSTOMER', phone_number__in=[phone, phone[4:]]).first()
    row, created = CustomerContact.objects.get_or_create(branch_id=branch_id, phone=phone)
    row = CustomerContact.objects.select_for_update().get(pk=row.pk)
    row.sources = sorted(set(row.sources) | {source})
    if name and name not in ('Guest', 'Walk-in Guest'):
        row.name = name[:150]
    if user:
        row.user = user
        row.name = user.get_full_name() or user.username or row.name
    row.last_seen = max(row.last_seen, seen or timezone.now()) if not created else seen or timezone.now()
    if login:
        row.last_login = timezone.now()
    row.save()


def record_login(user, outlet_id=None):
    if user.role != 'CUSTOMER':
        return
    branches = Branch.objects.filter(is_active=True, restaurant__is_active=True)
    if outlet_id and str(outlet_id).isdigit():
        branch = branches.filter(pk=outlet_id).first()
    elif user.restaurant_id:
        branch = branches.filter(restaurant_id=user.restaurant_id).order_by('-is_main_branch','id').first()
    else:
        # A legacy global account can be attributed only when its brand is unambiguous.
        ids = list(branches.values_list('restaurant_id', flat=True).distinct()[:2])
        branch = branches.order_by('-is_main_branch','id').first() if len(ids) == 1 else None
    if branch:
        remember_contact(branch.pk, user.phone_number, user.username, 'WEBSITE', user=user, login=True)


def record_visit(data):
    key = lambda value: salted_hmac('website-analytics', str(value), algorithm='sha256').hexdigest()
    row, created = WebsiteVisit.objects.get_or_create(pk=data['event_id'], defaults={
        'branch_id': data['outlet_id'], 'visitor_hash': key(data['visitor_id']),
        'session_hash': key(data['session_id']), 'path': data['path'],
        'channel': data['channel'], 'device': data['device']})
    return created
