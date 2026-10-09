"""Send a single targeted diagnostic using the real mobile order payload."""
import uuid
from django.core.management.base import BaseCommand, CommandError
from apps.orders.models import MobilePushDevice, Order
from apps.orders.pos_access import can_access
from apps.orders.push_notifications import send_new_order_push


class Command(BaseCommand):
    help = 'List registered phones or send one TEST order alert. Does not create or modify orders.'

    def add_arguments(self, parser):
        parser.add_argument('--list', action='store_true', dest='list_devices')
        parser.add_argument('--device', type=int, help='MobilePushDevice primary key, not the Firebase token')
        parser.add_argument('--order', type=int, help='Existing order ID in the same outlet, opened when tapped')

    def handle(self, *args, **options):
        devices = MobilePushDevice.objects.select_related('user', 'branch__restaurant')
        if options['list_devices']:
            if options['device'] or options['order']:
                raise CommandError('Use --list separately from --device and --order.')
            self.stdout.write('DEVICE  USER  OUTLET  ACTIVE  AUTHORIZED  LAST REGISTERED')
            count = 0
            for device in devices.order_by('pk').iterator():
                authorized = can_access(device.user, device.branch, 'read')
                self.stdout.write(f'{device.pk}  {device.user_id}  {device.branch_id}  {device.active}  {authorized}  {device.updated_at.isoformat()}')
                count += 1
            if not count:
                self.stdout.write('No registered phones. Sign in on the mobile app and allow registration to finish.')
            return
        if not options['device'] or not options['order']:
            raise CommandError('Use --list, or provide both --device ID and --order ID.')
        device = devices.filter(pk=options['device']).first()
        if not device or not device.active:
            raise CommandError('Device does not exist or is inactive.')
        if not device.branch.is_active or not device.branch.restaurant.is_active or not can_access(device.user, device.branch, 'read'):
            raise CommandError('Device user no longer has access to an active outlet.')
        order = Order.objects.filter(pk=options['order'], branch_id=device.branch_id).first()
        if not order:
            raise CommandError('Order does not exist in this device’s outlet.')
        event_id = uuid.uuid4()
        try:
            message_id = send_new_order_push(device.token, event_id, order.pk,
                f'TEST · {order.order_number}', device.branch_id, device.user_id)
        except Exception as exc:
            # Provider exceptions can contain device tokens. Print the safe class only.
            raise CommandError(f'FCM send failed ({type(exc).__name__}). Check FIREBASE_PROJECT_ID, ADC credentials, FCM permissions and device registration.') from None
        self.stdout.write(self.style.SUCCESS(f'Firebase accepted: {message_id}'))
        self.stdout.write(f'Device {device.pk}; user {device.user_id}; outlet {device.branch_id}; event {event_id}')
        self.stdout.write('Check the phone. Acceptance is not proof of display. This direct test bypasses Celery; create a normal order to test the full queue.')
