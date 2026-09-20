from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
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

