from django.db import models


class LoyaltyProgram(models.Model):
    restaurant = models.OneToOneField('restaurants.Restaurant', on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    tiers = models.JSONField(default=list)
    version = models.PositiveIntegerField(default=1)


class LoyaltyCustomer(models.Model):
    restaurant = models.ForeignKey('restaurants.Restaurant', on_delete=models.CASCADE)
    phone = models.CharField(max_length=20)
    name = models.CharField(max_length=150, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['restaurant', 'phone'], name='loyalty_restaurant_phone')]


class LoyaltyPurchase(models.Model):
    customer = models.ForeignKey(LoyaltyCustomer, on_delete=models.CASCADE, related_name='purchases')
    order = models.OneToOneField('orders.Order', on_delete=models.CASCADE, related_name='loyalty_purchase')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)
