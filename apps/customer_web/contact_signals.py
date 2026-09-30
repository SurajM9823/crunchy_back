from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.orders.models import Order
from apps.user_accounts.models import User
from .audience_services import remember_contact


@receiver(post_save, sender=Order)
def order_contact(sender, instance, raw=False, **kwargs):
    if not raw and instance.order_source in ('WEBSITE','TABLE_QR','KIOSK'):
        remember_contact(instance.branch_id, instance.customer_phone, instance.customer_name,
                         instance.order_source, seen=instance.updated_at)


@receiver(post_save, sender=User)
def link_registered_customer(sender, instance, raw=False, **kwargs):
    if not raw and instance.role == 'CUSTOMER':
        from .models import CustomerContact
        from .audience_services import normalized_phone
        phone = normalized_phone(instance.phone_number)
        if phone:
            CustomerContact.objects.filter(phone=phone).update(user=instance, name=instance.get_full_name() or instance.username)
