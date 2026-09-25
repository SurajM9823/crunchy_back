from decimal import Decimal
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from apps.common.models import TimeStampedModel
from apps.common.utils import normalize_phone_number


class UserRole(models.TextChoices):
    SUPERADMIN = 'SUPERADMIN', _('Super Administrator')
    RESTAURANT_OWNER = 'RESTAURANT_OWNER', _('Restaurant Owner')
    BRANCH_MANAGER = 'BRANCH_MANAGER', _('Branch Manager')
    CHEF = 'CHEF', _('Kitchen Chef')
    WAITER = 'WAITER', _('Waiter / Server')
    CASHIER = 'CASHIER', _('Cashier / Billing')
    RIDER = 'RIDER', _('Delivery Rider')
    CUSTOMER = 'CUSTOMER', _('Customer')


class UserManager(BaseUserManager):
    """
    Custom user manager that allows user creation with phone number,
    email, or username.
    """
    def create_user(self, username=None, email=None, phone_number=None, password=None, **extra_fields):
        if not username and not email and not phone_number:
            raise ValueError(_('User must have at least one identifier: username, email, or phone number.'))

        if email:
            email = self.normalize_email(email).lower()

        if phone_number:
            phone_number = normalize_phone_number(phone_number)

        # Fallback username generation if not provided
        if not username:
            if email:
                username = email.split('@')[0]
            elif phone_number:
                username = f"user_{phone_number[-6:]}"

            # Ensure uniqueness of fallback username
            base_username = username
            counter = 1
            while self.model.objects.filter(username=username).exists():
                username = f"{base_username}_{counter}"
                counter += 1

        user = self.model(
            username=username,
            email=email,
            phone_number=phone_number,
            **extra_fields
        )

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, username=None, email=None, phone_number=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', UserRole.SUPERADMIN)

        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))

        return self.create_user(
            username=username,
            email=email,
            phone_number=phone_number,
            password=password,
            **extra_fields
        )


class User(AbstractUser, TimeStampedModel):
    """
    Custom User Model for Crunchy RMS.
    Authenticates via phone number, email, or username.
    """
    username = models.CharField(
        _('username'),
        max_length=150,
        unique=True,
        null=True,
        blank=True,
        help_text=_('Required for standard auth. Letters, digits and @/./+/-/_ only.'),
    )
    email = models.EmailField(
        _('email address'),
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )
    phone_number = models.CharField(
        _('phone number'),
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text=_('Contact number including country code if applicable, e.g. +1234567890'),
    )
    role = models.CharField(
        _('user role'),
        max_length=30,
        choices=UserRole.choices,
        default=UserRole.CUSTOMER,
        db_index=True,
    )
    is_verified = models.BooleanField(
        _('is verified'),
        default=False,
        help_text=_('Designates whether the user has verified their phone number or email.'),
    )
    restaurant = models.ForeignKey(
        'restaurants.Restaurant',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='staff_members',
        verbose_name=_('assigned restaurant brand'),
    )
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='branch_staff_members',
        verbose_name=_('assigned outlet branch'),
    )

    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        ordering = ['-created_at']

    def __str__(self):
        primary = self.username or self.email or self.phone_number or f"User #{self.pk}"
        return f"{primary} ({self.get_role_display()})"

    def clean(self):
        super().clean()
        if self.email:
            self.email = self.__class__.objects.normalize_email(self.email).lower()
        if self.phone_number:
            self.phone_number = normalize_phone_number(self.phone_number)


class SystemRole(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", _("Super Admin")
    STORE_MANAGER = "STORE_MANAGER", _("Store Manager")
    CASHIER = "CASHIER", _("Cashier")
    KITCHEN_SUPERVISOR = "KITCHEN_SUPERVISOR", _("Kitchen Supervisor")
    INVENTORY_MANAGER = "INVENTORY_MANAGER", _("Inventory Manager")
    FLOOR_STAFF = "FLOOR_STAFF", _("Floor Staff")


class AdminSubPage(models.TextChoices):
    # Operations
    POS = "pos", _("POS & Orders")
    KITCHEN = "kitchen", _("Kitchen KDS")
    INVENTORY = "inventory", _("Inventory Stock")
    MENU = "menu", _("Menu Catalog")
    # Financial
    DAYBOOK = "daybook", _("Shift Daybook")
    PURCHASES = "purchases", _("Purchases & Invoices")
    LOYALTY = "loyalty", _("Loyalty & Khata")
    ANALYTICS = "analytics", _("Reports & KPIs")
    # Management
    EMPLOYEES = "employees", _("Staff & Access")
    OUTLETS = "outlets", _("Branch Outlets")
    ORGANIZATION = "organization", _("Fiscal & PAN")
    LOGS = "logs", _("Activity Logs")


ROLE_PRESET_MODULES = {
    SystemRole.CASHIER: ["pos", "daybook", "loyalty"],
    SystemRole.KITCHEN_SUPERVISOR: ["kitchen", "inventory"],
    SystemRole.INVENTORY_MANAGER: ["inventory", "purchases", "daybook"],
    SystemRole.STORE_MANAGER: ["pos", "kitchen", "inventory", "purchases", "daybook", "menu", "loyalty", "analytics"],
    SystemRole.SUPER_ADMIN: [page.value for page in AdminSubPage],
    SystemRole.FLOOR_STAFF: ["pos"],
}


class Employee(TimeStampedModel):
    """
    Staff & Access Profile for restaurant operations.
    Controls multi-branch assignment, payroll compliance, quick POS PIN,
    and granular Role-Based Access Control (assigned_pages).
    """
    id = models.CharField(max_length=64, primary_key=True)
    user = models.OneToOneField(
        'User',
        on_delete=models.CASCADE,
        related_name='employee_profile',
    )
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32)
    role = models.CharField(
        max_length=32,
        choices=SystemRole.choices,
        default=SystemRole.CASHIER,
        db_index=True,
    )
    title = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="e.g. Head Cashier & Shift Lead",
    )
    assigned_outlet = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.PROTECT,
        related_name='staff_members',
    )
    salary_monthly = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Base monthly salary in NPR",
    )
    assigned_pages = models.JSONField(
        default=list,
        help_text="List of AdminSubPage keys: ['pos', 'daybook', 'loyalty']",
    )
    pin_hash = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="Hashed 4-to-6 digit quick POS terminal switch PIN",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    joined_date = models.DateField(default=timezone.localdate)
    avatar = models.URLField(max_length=500, blank=True, null=True)

    class Meta:
        db_table = 'user_accounts_employee'
        ordering = ['-created_at']
        verbose_name = _('employee profile')
        verbose_name_plural = _('employee profiles')

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) - {self.assigned_outlet.name}"

    def set_pin(self, raw_pin: str):
        if raw_pin:
            self.pin_hash = make_password(str(raw_pin).strip())
        else:
            self.pin_hash = None

    def check_pin(self, raw_pin: str) -> bool:
        if not self.pin_hash or not raw_pin:
            return False
        return check_password(str(raw_pin).strip(), self.pin_hash)


