"""Recurring outlet khaja routines; weekday 0 is Sunday."""
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from apps.catalog.models import Product
from apps.orders.pos_services import Conflict
from apps.restaurants.models import Branch
from .models import KhajaScheduleDay

DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


class ScheduleInput(serializers.Serializer):
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    revision = serializers.IntegerField(min_value=0)
    product_ids = serializers.ListField(child=serializers.CharField(max_length=64), max_length=500, allow_empty=True)

    def validate_product_ids(self, value):
        return sorted(set(value))


def menu_products(branch):
    return Product.objects.filter(category__restaurant_id=branch.restaurant_id,
        is_archived=False, category__is_archived=False).select_related('category').order_by('category__display_order', 'name', 'pk')


def product_data(product):
    return {'id': product.pk, 'name': product.name, 'category': product.category.name,
            'archived': product.is_archived or product.category.is_archived}


def schedule_data(branch):
    rows = {row.weekday: row for row in KhajaScheduleDay.objects.filter(branch=branch)
            .select_related('updated_by').prefetch_related('products__category')}
    days = []
    for weekday, name in enumerate(DAYS):
        row = rows.get(weekday)
        days.append({'weekday': weekday, 'name': name, 'revision': row.revision if row else 0,
            'products': [product_data(p) for p in row.products.all()] if row else [],
            'updated_at': row.updated_at.isoformat() if row else None,
            'updated_by': (row.updated_by.get_full_name() or row.updated_by.username) if row else None})
    return {'days': days, 'products': [product_data(p) for p in menu_products(branch)]}


@transaction.atomic
def save_day(branch, actor, data):
    Branch.objects.select_for_update().get(pk=branch.pk)
    row = KhajaScheduleDay.objects.filter(branch=branch, weekday=data['weekday']).first()
    existing = set(row.products.values_list('pk', flat=True)) if row else set()
    requested = set(data['product_ids'])
    # Repeating a successful save is safe, including a retry after a lost response.
    if row and requested == existing:
        return schedule_data(branch)
    if data['revision'] != (row.revision if row else 0):
        raise Conflict('This day was edited elsewhere. Close the editor, refresh the schedule, and try again.')
    valid = set(menu_products(branch).filter(pk__in=requested).values_list('pk', flat=True))
    if requested - valid - existing:
        raise ValidationError({'product_ids': 'Choose menu products belonging to this outlet. Archived products cannot be added.'})
    if row is None:
        row = KhajaScheduleDay(branch=branch, weekday=data['weekday'])
    row.updated_by = actor
    row.revision += 1
    row.save()
    row.products.set(sorted(requested))
    return schedule_data(branch)
