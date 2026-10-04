from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.orders.models import Order
from .services import record_purchase


@receiver(post_save, sender=Order)
def order_purchase(sender, instance, raw=False, **kwargs):
    if not raw:
        record_purchase(instance)
