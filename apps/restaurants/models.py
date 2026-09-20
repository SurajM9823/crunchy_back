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
    name = models.CharField(
        _('brand name'),
        max_length=150,
        unique=True,
        db_index=True,
        help_text=_('Unique brand name of the restaurant (e.g., "Crunchy Bag")'),
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
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_branches',
        verbose_name=_('branch manager'),
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

