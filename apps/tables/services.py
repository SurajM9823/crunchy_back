import uuid
from django.db import transaction
from .models import DiningTable


@transaction.atomic
def table_create(
    branch,
    table_number: str,
    capacity: int = 4,
    section: str = "Main Hall",
    is_active: bool = True,
) -> DiningTable:
    table = DiningTable(
        branch=branch,
        table_number=table_number.strip(),
        capacity=capacity,
        section=section.strip(),
        is_active=is_active,
    )
    table.save()
    return table


@transaction.atomic
def table_update(table: DiningTable, **kwargs) -> DiningTable:
    for key, value in kwargs.items():
        if hasattr(table, key):
            setattr(table, key, value)
    table.save()
    return table


@transaction.atomic
def table_regenerate_qr_salt(table: DiningTable) -> DiningTable:
    """
    Regenerates QR salt, immediately invalidating any previously printed or cached QR tokens.
    """
    table.qr_token_salt = uuid.uuid4().hex[:16]
    table.save()
    return table


@transaction.atomic
def table_open_dining_session(table: DiningTable) -> uuid.UUID:
    """
    Opens an active dining session round on this table if none exists.
    """
    if not table.active_session_id:
        table.active_session_id = uuid.uuid4()
        table.save(update_fields=['active_session_id', 'updated_at'])
    return table.active_session_id


@transaction.atomic
def table_close_dining_session(table: DiningTable):
    """
    Closes the dining tab session after bill checkout/settlement.
    """
    table.active_session_id = None
    table.save(update_fields=['active_session_id', 'updated_at'])

