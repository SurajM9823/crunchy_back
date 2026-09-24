from decimal import Decimal
from django.db import models
from django.conf import settings
from apps.common.models import TimeStampedModel


class VehicleType(models.TextChoices):
    MOTORCYCLE = 'MOTORCYCLE', 'Motorcycle'
    SCOOTER = 'SCOOTER', 'Scooter'
    BICYCLE = 'BICYCLE', 'Bicycle'
    ELECTRIC_EV = 'ELECTRIC_EV', 'Electric EV'
    CAR = 'CAR', 'Car / Van'


class RiderStatus(models.TextChoices):
    OFFLINE = 'OFFLINE', 'Offline'
    AVAILABLE = 'AVAILABLE', 'Available for Orders'
    BUSY = 'BUSY', 'On Active Delivery'


class DispatchStatus(models.TextChoices):
    UNASSIGNED = 'UNASSIGNED', 'Waiting for Rider Assignment'
    ASSIGNED = 'ASSIGNED', 'Rider Assigned'
    ACCEPTED = 'ACCEPTED', 'Rider Accepted Order'
    PICKED_UP = 'PICKED_UP', 'Picked Up from Kitchen (Out for Delivery)'
    ARRIVED = 'ARRIVED', 'Arrived at Customer Location'
    DELIVERED = 'DELIVERED', 'Delivered & Handed Over'
    FAILED = 'FAILED', 'Delivery Failed / Customer Unreachable'


class DeliveryZone(TimeStampedModel):
    """
    Branch service delivery zone.
    Configures radial boundaries, delivery surcharges, and estimated fulfillment times.
    """
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='delivery_zones',
        db_index=True,
    )
    name = models.CharField(
        max_length=120,
        help_text="Zone name (e.g. Core City 5km, Outer Ring Road)",
    )
    radius_km = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('5.00'),
        help_text="Service delivery radius in kilometers",
    )
    center_lat = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        help_text="Latitude coordinate of branch / zone hub",
    )
    center_lng = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        help_text="Longitude coordinate of branch / zone hub",
    )
    delivery_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('50.00'),
        help_text="Standard delivery fee in NPR",
    )
    min_order_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Minimum cart subtotal required for delivery",
    )
    free_delivery_threshold = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Subtotal above which delivery fee is waived",
    )
    estimated_delivery_minutes = models.PositiveIntegerField(
        default=35,
        help_text="Estimated door-to-door delivery duration displayed to customers",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'delivery_zone'
        verbose_name = 'Delivery Zone'
        verbose_name_plural = 'Delivery Zones'
        ordering = ['branch', 'radius_km']

    def __str__(self):
        return f"{self.branch.name} - {self.name} ({self.radius_km} km)"


class RiderProfile(TimeStampedModel):
    """
    Fleet rider profile linked to a user account.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='rider_profile',
    )
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='riders',
        help_text="Primary hub / branch assignment",
    )
    vehicle_type = models.CharField(
        max_length=32,
        choices=VehicleType.choices,
        default=VehicleType.MOTORCYCLE,
    )
    vehicle_plate = models.CharField(
        max_length=32,
        help_text="Official vehicle registration / license plate number",
    )
    status = models.CharField(
        max_length=32,
        choices=RiderStatus.choices,
        default=RiderStatus.OFFLINE,
        db_index=True,
    )
    current_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    current_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    total_deliveries = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'delivery_rider_profile'
        verbose_name = 'Rider Profile'
        verbose_name_plural = 'Rider Profiles'

    def __str__(self):
        return f"Rider: {self.user.get_full_name() or self.user.username} ({self.vehicle_plate}) [{self.status}]"


class DeliveryDispatch(TimeStampedModel):
    """
    Delivery tracking order dispatch record.
    Manages rider assignment, kitchen pickup, customer transit, and live location.
    """
    dispatch_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique dispatch reference (e.g. DSP-260924-0001)",
    )
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.CASCADE,
        related_name='delivery_dispatch',
        db_index=True,
    )
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='dispatches',
        db_index=True,
    )
    rider = models.ForeignKey(
        RiderProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_deliveries',
        db_index=True,
    )
    zone = models.ForeignKey(
        DeliveryZone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dispatches',
    )
    status = models.CharField(
        max_length=32,
        choices=DispatchStatus.choices,
        default=DispatchStatus.UNASSIGNED,
        db_index=True,
    )
    delivery_address = models.TextField(help_text="Destination address provided by customer")
    destination_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    destination_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    rider_notes = models.CharField(max_length=255, blank=True, default="")

    assigned_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'delivery_dispatch'
        verbose_name = 'Delivery Dispatch'
        verbose_name_plural = 'Delivery Dispatches'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.dispatch_id} ({self.order.order_number}) -> {self.status}"

