import hashlib
import re
import secrets
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.restaurants.models import Branch
from apps.orders.pos_access import staff_branch, require_access
from .models import Conversation


def guest_hash(request):
    token = request.headers.get('X-Chat-Guest', '')
    if not re.fullmatch(r'[a-f0-9]{64}', token):
        raise ValidationError('A valid guest chat credential is required.')
    return hashlib.sha256(token.encode()).hexdigest()


def customer_branch(request):
    if not request.user.is_authenticated:
        branch_id = settings.CHAT_GUEST_OUTLET_ID
    else:
        user = request.user
        employee = getattr(user, 'employee_profile', None)
        branch_id = user.branch_id or (employee.assigned_outlet_id if employee else None)
        # Customer accounts without a fixed branch use their selected storefront outlet.
        if not branch_id:
            branch_id = request.data.get('outlet_id') or request.query_params.get('outlet_id') or settings.CHAT_GUEST_OUTLET_ID
        if not str(branch_id).isdigit():
            raise ValidationError('Choose a valid outlet.')
    branch = get_object_or_404(Branch, pk=branch_id, is_active=True, restaurant__is_active=True)
    if request.user.is_authenticated and request.user.restaurant_id and request.user.restaurant_id != branch.restaurant_id:
        raise PermissionDenied('This outlet is outside your account.')
    return branch


def conversation_for(request, conversation_id, staff=False):
    thread = get_object_or_404(Conversation.objects.select_related('branch__restaurant'), pk=conversation_id,
        branch__is_active=True, branch__restaurant__is_active=True)
    if staff:
        branch = staff_branch(request, 'orders')
        if branch.pk != thread.branch_id: raise PermissionDenied('Conversation unavailable.')
    elif request.user.is_authenticated:
        if thread.customer_id != request.user.pk: raise PermissionDenied('Conversation unavailable.')
    elif thread.customer_id or not secrets.compare_digest(thread.guest_hash, guest_hash(request)):
        raise PermissionDenied('Conversation unavailable.')
    return thread
