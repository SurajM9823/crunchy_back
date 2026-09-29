from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from apps.common.models import TimeStampedModel


class Restaurant(TimeStampedModel):
    """
    The Parent Restaurant Brand / Franchise entity.
    All franchise branches/outlets belong to this parent brand.
    """
    payment_qr = models.ImageField(upload_to='payment_qr/', blank=True, null=True)
    name = models.CharField(
        _('brand name'),
        max_length=150,
        unique=True,
        db_index=True,
        help_text=_('Unique brand name of the restaurant (e.g., "Crunchy Bag")'),
    )
    legal_name = models.CharField(
        _('registered legal entity name'),
        max_length=200,
        blank=True,
        default='',
        help_text=_('Official registered legal company name, e.g. "Crunchy Bag Food & Hospitality Pvt. Ltd."'),
    )
    slug = models.SlugField(
        _('brand slug'),
        max_length=160,
        unique=True,
        db_index=True,
        help_text=_('URL-friendly unique slug for the brand'),
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='administered_restaurants',
        verbose_name=_('restaurant admin / owner'),
        help_text=_('The primary restaurant owner/admin user responsible for this brand.'),
    )
    pan_number = models.CharField(
        _('PAN / VAT number'),
        max_length=30,
        blank=True,
        default='',
        help_text=_('Official PAN or Tax Registration number for fiscal billing'),
    )
    phone = models.CharField(
        _('primary phone'),
        max_length=20,
        blank=True,
        default='',
    )
    email = models.EmailField(
        _('brand contact email'),
        blank=True,
        default='',
    )
    website = models.URLField(
        _('official website'),
        blank=True,
        default='',
    )
    address = models.CharField(
        _('head office address'),
        max_length=255,
        blank=True,
        default='',
        help_text=_('Registered address / Head office location'),
    )
    logo = models.ImageField(
        _('brand logo file'),
        upload_to='organization/logos/',
        blank=True,
        null=True,
        help_text=_('Uploaded brand logo image file'),
    )
    logo_url = models.URLField(
        _('brand logo URL'),
        blank=True,
        default='',
    )
    description = models.TextField(
        _('brand description'),
        blank=True,
        default='',
    )
    currency = models.CharField(
        _('currency code'),
        max_length=10,
        default='NPR',
        help_text=_('Operational currency ISO code (e.g. NPR)'),
    )
    currency_symbol = models.CharField(
        _('currency symbol'),
        max_length=10,
        default='रु',
        help_text=_('Currency symbol (e.g. रु or Rs.)'),
    )

    # Fiscal & Tax Rules (Inland Revenue Department - IRD Nepal)
    vat_rate_percent = models.DecimalField(
        _('VAT rate (%)'),
        max_digits=5,
        decimal_places=2,
        default=Decimal('13.00'),
        help_text=_('Inland Revenue Department standard VAT rate (typically 13%)'),
    )
    is_vat_enabled = models.BooleanField(
        _('is VAT enabled'),
        default=True,
        help_text=_('Whether statutory VAT is charged on bills'),
    )
    service_charge_percent = models.DecimalField(
        _('service charge (%)'),
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_('Hospitality service charge percentage (e.g. 0% or 10%)'),
    )
    is_service_charge_enabled = models.BooleanField(
        _('is service charge enabled'),
        default=False,
        help_text=_('Whether restaurant service charge is levied on dining orders'),
    )
    ird_bill_prefix = models.CharField(
        _('IRD invoice bill prefix'),
        max_length=20,
        default='CB-INV-',
        help_text=_('Prefix for statutory fiscal bills (e.g. "CB-INV-")'),
    )
    fiscal_year = models.CharField(
        _('current fiscal year'),
        max_length=20,
        default='2081/82',
        help_text=_('Nepal Bikram Sambat fiscal accounting year (e.g. 2081/82)'),
    )
    ird_software_id = models.CharField(
        _('IRD software identifier'),
        max_length=64,
        blank=True,
        default='',
        help_text=_('Registered CBMS IRD software registration ID'),
    )
    ird_enable_realtime_sync = models.BooleanField(
        _('enable IRD real-time sync'),
        default=False,
        help_text=_('Flag to sync bills in real-time with IRD CBMS portal'),
    )

    # Payment Methods & Gateways
    enable_cash = models.BooleanField(
        _('enable cash payment'),
        default=True,
        help_text=_('Accept physical cash at cashier counter'),
    )
    enable_card = models.BooleanField(
        _('enable card / POS swipe'),
        default=True,
        help_text=_('Accept credit/debit card swipe POS terminal'),
    )
    enable_fonepay = models.BooleanField(
        _('enable Fonepay QR'),
        default=True,
        help_text=_('Accept dynamic and static Fonepay QR network payments'),
    )
    enable_esewa = models.BooleanField(
        _('enable eSewa wallet'),
        default=True,
        help_text=_('Accept eSewa digital wallet QR and web checkout'),
    )
    enable_khalti = models.BooleanField(
        _('enable Khalti wallet'),
        default=True,
        help_text=_('Accept Khalti digital wallet QR and web checkout'),
    )

    is_active = models.BooleanField(
        _('is active'),
        default=True,
        help_text=_('Whether this restaurant brand is active and operating.'),
    )

    class Meta:
        verbose_name = _('restaurant brand')
        verbose_name_plural = _('restaurant brands')
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure unique slug
            base_slug = self.slug
            counter = 1
            while Restaurant.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)

    @property
    def branches_count(self) -> int:
        return self.branches.count()


class OperateType(models.TextChoices):
    DINE_IN = 'DINE_IN', _('Full Dine-In Restaurant')
    EXPRESS_TAKEOUT = 'EXPRESS_TAKEOUT', _('Express QSR / Takeaway')
    CLOUD_KITCHEN = 'CLOUD_KITCHEN', _('Cloud / Ghost Kitchen (Delivery Only)')
    DRIVE_THRU = 'DRIVE_THRU', _('Drive-Thru & Pick-up')
    FOOD_TRUCK_KIOSK = 'FOOD_TRUCK_KIOSK', _('Food Truck / Kiosk')
    HYBRID = 'HYBRID', _('Hybrid Multi-Channel Hub')


class Branch(TimeStampedModel):
    """
    An individual outlet or franchise branch of a Restaurant Brand.
    Each outlet shares the parent brand, with its own location and local operations.
    """
    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        related_name='branches',
        verbose_name=_('parent restaurant brand'),
    )
    name = models.CharField(
        _('outlet name'),
        max_length=150,
        help_text=_('Outlet name, e.g., "Kathmandu Flagship", "Lalitpur Hub", "Airport Express"'),
    )
    branch_code = models.CharField(
        _('branch code'),
        max_length=50,
        unique=True,
        db_index=True,
        help_text=_('Unique operational code, e.g. "CB-KTM-001"'),
    )
    operate_type = models.CharField(
        _('operate type'),
        max_length=30,
        choices=OperateType.choices,
        default=OperateType.DINE_IN,
        db_index=True,
        help_text=_('Operating model for this outlet (Dine-in, Cloud Kitchen, Express Takeout, etc.)'),
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_branches',
        verbose_name=_('outlet admin / manager'),
        help_text=_('The assigned Outlet Administrator responsible for local operations.'),
    )
    # Channel Capabilities
    enable_dine_in = models.BooleanField(
        _('enable dine-in'),
        default=True,
        help_text=_('Allow table seating and waiter service at this outlet.'),
    )
    enable_takeaway = models.BooleanField(
        _('enable takeaway'),
        default=True,
        help_text=_('Allow counter pick-up / takeaway orders.'),
    )
    enable_delivery = models.BooleanField(
        _('enable delivery'),
        default=True,
        help_text=_('Allow delivery fulfillment from this outlet.'),
    )
    enable_drive_thru = models.BooleanField(
        _('enable drive-thru'),
        default=False,
        help_text=_('Allow vehicular drive-thru lane ordering.'),
    )
    enable_qr_ordering = models.BooleanField(
        _('enable QR digital ordering'),
        default=True,
        help_text=_('Allow customers to scan table QR codes and place orders.'),
    )
    enable_kiosk = models.BooleanField(
        _('enable self-order kiosk'),
        default=False,
        help_text=_('Enable self-ordering touch kiosks at this outlet.'),
    )
    enable_pos = models.BooleanField(
        _('enable cashier POS'),
        default=True,
        help_text=_('Enable cashier billing terminal at this outlet.'),
    )
    phone_number = models.CharField(
        _('outlet phone'),
        max_length=20,
        blank=True,
        default='',
    )
    email = models.EmailField(
        _('outlet email'),
        blank=True,
        default='',
    )
    address_line = models.CharField(
        _('street address'),
        max_length=255,
        blank=True,
        default='',
    )
    city = models.CharField(
        _('city'),
        max_length=100,
        default='Kathmandu',
    )
    state = models.CharField(
        _('state / province'),
        max_length=100,
        blank=True,
        default='Bagmati',
    )
    postal_code = models.CharField(
        _('postal code'),
        max_length=20,
        blank=True,
        default='',
    )
    is_main_branch = models.BooleanField(
        _('is main branch'),
        default=False,
        help_text=_('Designates if this is the primary/flagship branch for the brand.'),
    )
    is_active = models.BooleanField(
        _('is active'),
        default=True,
        help_text=_('Master operational status for this outlet.'),
    )
    accepting_orders = models.BooleanField(
        _('accepting orders'),
        default=True,
        help_text=_('Temporary toggle for kitchen overload or operational pauses.'),
    )

    class Meta:
        verbose_name = _('branch outlet')
        verbose_name_plural = _('branch outlets')
        ordering = ['restaurant', 'name']
        unique_together = ('restaurant', 'name')

    def __str__(self):
        return f"{self.restaurant.name} — {self.name} ({self.branch_code})"

