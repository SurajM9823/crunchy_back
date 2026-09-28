"""Outlet-scoped, idempotent floor configuration for the staff POS."""
import hashlib
import json
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, NotFound
from apps.restaurants.models import Branch
from apps.orders.models import Order, PosMutation
from apps.orders.pos_selectors import ACTIVE
from apps.orders.pos_services import Conflict
from apps.catalog.services import menu_changed
from .models import DiningTable, TableGroup


class GroupInput(serializers.Serializer):
    name = serializers.CharField(max_length=64)


class TableInput(serializers.Serializer):
    table_number = serializers.CharField(max_length=32)
    capacity = serializers.IntegerField(min_value=1, max_value=1000)
    group_id = serializers.IntegerField(min_value=1)
    is_active = serializers.BooleanField(default=True)


@transaction.atomic
def save_layout(branch, actor, key, kind, data, object_id=None):
    if not key or len(key) > 128:
        raise ValidationError('An Idempotency-Key is required.')
    Branch.objects.select_for_update().get(pk=branch.pk)
    fingerprint = hashlib.sha256(json.dumps([actor.pk, kind, object_id, data], sort_keys=True).encode()).hexdigest()
    previous = PosMutation.objects.filter(branch=branch, key=key).first()
    if previous:
        if previous.fingerprint != fingerprint:
            raise Conflict('This request key was already used for a different change.')
        return previous.response
    model = TableGroup if kind == 'group' else DiningTable
    obj = model.objects.filter(branch=branch, pk=object_id).first() if object_id else model(branch=branch)
    if obj is None:
        raise NotFound('Configuration not found at this outlet.')
    if kind == 'group':
        if TableGroup.objects.filter(branch=branch, name__iexact=data['name']).exclude(pk=obj.pk).exists():
            raise ValidationError({'name': 'This group already exists.'})
        if obj.pk:
            DiningTable.objects.filter(branch=branch, section=obj.name).update(section=data['name'])
        obj.name = data['name']
        obj.save()
        result = {'id': obj.pk, 'name': obj.name}
    else:
        group = TableGroup.objects.filter(branch=branch, pk=data['group_id']).first()
        if not group:
            raise ValidationError({'group_id': 'Choose a group at this outlet.'})
        if DiningTable.objects.filter(branch=branch, table_number__iexact=data['table_number']).exclude(pk=obj.pk).exists():
            raise ValidationError({'table_number': 'This table label already exists.'})
        if obj.pk and Order.objects.filter(branch=branch, table=obj, status__in=ACTIVE).exists():
            raise Conflict('Complete this table\'s running order before changing its configuration.')
        obj.table_number, obj.capacity, obj.section = data['table_number'], data['capacity'], group.name
        obj.is_active = data['is_active']
        obj.save()
        result = {'id': obj.pk, 'table_number': obj.table_number}
    # Durable revision/outbox also lets disconnected terminals recover configuration changes.
    menu_changed(branch_id=branch.pk)
    PosMutation.objects.create(branch=branch, key=key, fingerprint=fingerprint, response=result)
    return result
