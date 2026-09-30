from django.db import transaction

from .models import OrderTokenSequence


SOURCE_PREFIXES = {
    'WEBSITE': 'W',
    'KIOSK': 'K',
    'TABLE_QR': 'QR',
    'POS': 'POS',
}


@transaction.atomic
def generate_order_number(order_source: str) -> str:
    prefix = SOURCE_PREFIXES[order_source]
    sequence = OrderTokenSequence.objects.select_for_update().get(source=order_source)
    sequence.counter += 1
    sequence.save(update_fields=['counter'])
    return f'{prefix}-{sequence.counter:02d}'