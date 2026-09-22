import uuid
from django.db import models
from apps.common.models import TimeStampedModel


class DiningTable(TimeStampedModel):
    """
    Physical restaurant dining table linked to a specific franchise outlet.
    Enables table QR ordering, POS assignment, and running table tabs.
    """
    branch = models.ForeignKey(
        'restaurants.Branch',
        on_delete=models.CASCADE,
        related_name='dining_tables',
        db_index=True,
    )
    table_number = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Human readable table label (e.g. T-01, Booth 4, Patio 2)",
    )
    capacity = models.PositiveSmallIntegerField(default=4)
    section = models.CharField(
        max_length=64,
        default="Main Hall",
        help_text="Physical zone (e.g. Main Hall, Terrace, Rooftop, Bar)",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Toggle table active or under maintenance",
    )
    qr_token_salt = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Unique table salt for cryptographic opaque QR token generation",
    )
    active_session_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID of active running dining tab session",
    )

    class Meta:
        db_table = 'dining_table'
        verbose_name = 'Dining Table'
        verbose_name_plural = 'Dining Tables'
        unique_together = ('branch', 'table_number')
        ordering = ['branch', 'table_number']

    def save(self, *args, **kwargs):
        if not self.qr_token_salt:
            self.qr_token_salt = uuid.uuid4().hex[:16]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.branch.name} - Table {self.table_number}"

