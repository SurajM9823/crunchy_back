from django.db.models import Count, F, OuterRef, Subquery, Value, BigIntegerField
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError
from .models import Conversation, Message, ReadReceipt


def number(raw, default=0):
    if raw is None or raw == '': return default
    try: value = int(raw)
    except (ValueError, TypeError): raise ValidationError('Invalid message cursor.')
    if value < 0 or value > 9223372036854775807: raise ValidationError('Invalid message cursor.')
    return value


def message_data(message):
    return {'id': message.pk, 'conversation_id': str(message.conversation_id), 'client_id': str(message.client_id),
        'text': message.text, 'is_staff': message.is_staff, 'created_at': message.created_at.isoformat()}


def thread_data(thread, user=None, staff=False, state=None):
    if state is None:
        cursor = (ReadReceipt.objects.filter(conversation=thread, user=user).values_list('last_message_id', flat=True).first() or 0) if staff else thread.customer_read_id
        latest = thread.messages.order_by('-id').first()
        staff_read = ReadReceipt.objects.filter(conversation=thread).order_by('-last_message_id').values_list('last_message_id', flat=True).first() or 0
        unread = thread.messages.filter(id__gt=cursor, is_staff=not staff).count()
    else:
        cursor, latest, staff_read, unread = state
    return {'id': str(thread.pk), 'outlet_id': thread.branch_id, 'outlet_name': thread.branch.name,
        'customer_name': thread.customer_name, 'is_guest': thread.customer_id is None,
        'updated_at': thread.updated_at.isoformat(), 'last_message_id': thread.last_message_id,
        'customer_read_id': thread.customer_read_id, 'own_read_id': cursor,
        'staff_read_id': staff_read, 'unread_count': unread,
        'last_message': message_data(latest) if latest else None, 'revision': thread.revision}


def history(thread, params, user=None, staff=False):
    before = number(params.get('before'))
    after = number(params.get('after'))
    if before and after: raise ValidationError('Use one history cursor at a time.')
    if params.get('after') is not None:
        rows = list(thread.messages.filter(id__gt=after).order_by('id')[:101])
        return {'conversation': thread_data(thread, user, staff), 'messages': [message_data(m) for m in rows[:100]], 'has_newer': len(rows) > 100, 'has_more': False}
    rows = thread.messages.all()
    if before: rows = rows.filter(id__lt=before)
    rows = list(rows.order_by('-id')[:51])
    return {'conversation': thread_data(thread, user, staff), 'messages': [message_data(m) for m in reversed(rows[:50])], 'has_more': len(rows) > 50}


def inbox(branch, user, params):
    threads = Conversation.objects.filter(branch=branch, last_message_id__gt=0).select_related('branch')
    total_unread = Message.objects.filter(conversation__branch=branch, is_staff=False).annotate(
        cursor=Coalesce(Subquery(ReadReceipt.objects.filter(conversation_id=OuterRef('conversation_id'), user=user).values('last_message_id')[:1], output_field=BigIntegerField()), Value(0), output_field=BigIntegerField())).filter(id__gt=F('cursor')).count()
    before = number(params.get('before'))
    if before: threads = threads.filter(last_message_id__lt=before)
    search = params.get('search', '').strip()[:100]
    if search: threads = threads.filter(customer_name__icontains=search)
    rows = list(threads.order_by('-last_message_id')[:31])
    page = rows[:30]
    latest = {m.pk: m for m in Message.objects.filter(pk__in=[t.last_message_id for t in page])}
    own, seen = {}, {}
    for receipt in ReadReceipt.objects.filter(conversation__in=page):
        if receipt.user_id == user.pk: own[receipt.conversation_id] = receipt.last_message_id
        seen[receipt.conversation_id] = max(seen.get(receipt.conversation_id, 0), receipt.last_message_id)
    unread_rows = Message.objects.filter(conversation__in=page, is_staff=False).annotate(
        cursor=Coalesce(Subquery(ReadReceipt.objects.filter(conversation_id=OuterRef('conversation_id'), user=user).values('last_message_id')[:1], output_field=BigIntegerField()), Value(0), output_field=BigIntegerField())).filter(id__gt=F('cursor')).values('conversation_id').annotate(count=Count('id'))
    unread = {r['conversation_id']: r['count'] for r in unread_rows}
    return {'results': [thread_data(t, user, True, (own.get(t.pk, 0), latest.get(t.last_message_id), seen.get(t.pk, 0), unread.get(t.pk, 0))) for t in page], 'has_more': len(rows) > 30, 'unread_count': total_unread}
