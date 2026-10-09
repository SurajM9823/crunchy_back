"""Short, transactional event ingestion; browser outcomes cannot forge sales."""
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import ValidationError
from apps.restaurants.models import Branch
from .models import JourneySession, JourneyEvent
from .audience_services import record_visit

def identity_hash(value):
    return salted_hmac('website-analytics', str(value), algorithm='sha256').hexdigest()

@transaction.atomic
def ingest(data):
    branch = Branch.objects.filter(pk=data['outlet_id'], is_active=True, restaurant__is_active=True).first()
    if not branch:
        raise ValidationError('Outlet unavailable.')
    visitor = identity_hash(data['visitor_id'])
    previous = JourneySession.objects.filter(branch=branch, visitor_hash=visitor).order_by('first_seen').first()
    first = min(event['timestamp'] for event in data['events'])
    session, _ = JourneySession.objects.get_or_create(branch=branch, session_hash=identity_hash(data['session_id']), defaults={
        'visitor_hash': visitor, 'first_seen':first, 'last_seen':first, 'landing_path':data['events'][0]['path'],
        'device':data['device'], 'browser':data['browser'], 'os':data['os'], 'network':data['network'],
        'returning':bool(previous), 'first_touch':previous.first_touch if previous else data['attribution'],
        'session_touch':data['attribution'], 'last_touch':data['attribution'] or (previous.last_touch if previous else {})})
    session = JourneySession.objects.select_for_update().get(pk=session.pk)
    if session.visitor_hash != visitor:
        raise ValidationError('Session identity mismatch.')
    accepted = 0
    for event in data['events']:
        row, created = JourneyEvent.objects.get_or_create(pk=event['event_id'], defaults={
            'session':session, 'name':event['name'], 'occurred_at':event['timestamp'], 'path':event['path'], 'metadata':event['metadata']})
        if not created:
            if row.session_id != session.pk or row.name != event['name']:
                raise ValidationError('Event ID already belongs to a different event.')
            continue
        accepted += 1
        session.last_seen = max(session.last_seen, event['timestamp'])
        if event['name'] == 'page_view':
            record_visit({**data, 'event_id':event['event_id'], 'path':event['path'][:100], 'channel':'WEBSITE', 'device':data['device']})
    if accepted:
        earliest=min(data['events'],key=lambda row:row['timestamp'])
        if earliest['timestamp']<session.first_seen:
            session.first_seen=earliest['timestamp']
            session.landing_path=earliest['path']
        if session.device=='UNKNOWN':
            session.device,session.browser,session.os,session.network=data['device'],data['browser'],data['os'],data['network']
        if data['attribution']:
            session.last_touch = data['attribution']
            if not session.session_touch: session.session_touch=data['attribution']
            if not session.first_touch: session.first_touch=data['attribution']
        session.save(update_fields=['first_seen','landing_path','last_seen', 'first_touch','session_touch','last_touch','device','browser','os','network'])
    return {'accepted':accepted, 'duplicates':len(data['events'])-accepted}

def attach_order(order, context):
    """Called inside checkout's existing transaction. No PII or payment proof copied."""
    from django.conf import settings
    if settings.WEBSITE_ANALYTICS_PROVIDER == 'posthog':
        from .posthog_services import attach_posthog_order
        return attach_posthog_order(order, context)
    if not context:
        return
    visitor=identity_hash(context['visitor_id'])
    session,_ = JourneySession.objects.get_or_create(branch=order.branch, session_hash=identity_hash(context['session_id']),
        defaults={'visitor_hash':visitor,'landing_path':'/checkout'})
    if session.visitor_hash==visitor:
        JourneyEvent.objects.get_or_create(order=order, name='order_success', trusted=True, defaults={
            'session':session, 'path':'/checkout', 'metadata':{'cart_value':str(order.total_payable),
            'items':[{'product_id':str(item.product_id), 'product_name':item.product_name, 'quantity':item.quantity,
                      'unit_price':str(item.unit_price)} for item in order.items.filter(is_voided=False)]}})

def sync_order_outcomes(order):
    from django.conf import settings
    if settings.WEBSITE_ANALYTICS_PROVIDER == 'posthog':
        from .posthog_services import sync_posthog_order
        return sync_posthog_order(order)
    origin = JourneyEvent.objects.filter(order=order, name='order_success', trusted=True).first()
    if not origin:
        return
    names = []
    if order.status not in ('PENDING', 'CANCELLED'):
        names.append('order_confirmed')
    if order.paid_amount >= order.total_payable and order.paid_amount > 0:
        names.append('payment_success')
    for name in names:
        JourneyEvent.objects.get_or_create(order=order, name=name, trusted=True, defaults={
            'session':origin.session, 'path':'/checkout', 'metadata':{'cart_value':str(order.total_payable), 'payment_method':order.payment_method}})

def request_report(branch, filters):
    import hashlib
    import json
    from datetime import timedelta
    from django.core.serializers.json import DjangoJSONEncoder
    from django.db.models import Max, Count
    from apps.orders.models import Order
    from .models import JourneyReport, AdMetric
    events=JourneyEvent.objects.filter(session__branch=branch).aggregate(last=Max('received_at'), count=Count('pk'))
    orders=Order.objects.filter(branch=branch,order_source='WEBSITE').aggregate(last=Max('updated_at'),count=Count('pk'))
    ads=list(AdMetric.objects.filter(branch=branch).order_by('pk').values())
    normalized=json.loads(json.dumps(filters,cls=DjangoJSONEncoder))
    key=hashlib.sha256(json.dumps([branch.pk, normalized, events, orders, ads],sort_keys=True,cls=DjangoJSONEncoder).encode()).hexdigest()
    report,_=JourneyReport.objects.get_or_create(key=key,defaults={'branch':branch,'filters':normalized})
    # Refresh time-dependent live/abandonment classifications without creating an endless
    # chain of new report IDs when the completion notification reaches the dashboard.
    if report.status=='READY' and report.completed_at and report.completed_at<timezone.now()-timedelta(minutes=1):
        JourneyReport.objects.filter(pk=report.pk,status='READY').update(status='PENDING',attempts=0)
        report.status='PENDING'
    # The database is the durable queue. Beat processes pending jobs even after broker outages.
    return report
