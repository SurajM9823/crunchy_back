"""Send diagnostic push alerts using the real mobile order payload."""
import uuid
from django.core.management.base import BaseCommand, CommandError
from apps.orders.models import MobilePushDevice, Order
from apps.orders.pos_access import can_access
from apps.orders.push_notifications import send_new_order_push


class Command(BaseCommand):
    help = 'List registered phones or send TEST order push alerts to devices logged into an outlet.'

    def add_arguments(self, parser):
        parser.add_argument('--list', action='store_true', dest='list_devices', help='List all registered push devices')
        parser.add_argument('--outlet', type=int, help='Send dummy test alert to all active logged-in devices in this outlet (e.g. --outlet 1)')
        parser.add_argument('--device', type=int, help='MobilePushDevice primary key, not the Firebase token')
        parser.add_argument('--order', type=int, help='Existing order ID in the same outlet (optional; defaults to latest order in outlet)')
        parser.add_argument('--dry-run', action='store_true', help='Validate active devices and build payload without calling Firebase')

    def handle(self, *args, **options):
        devices = MobilePushDevice.objects.select_related('user', 'branch__restaurant')

        if options['list_devices']:
            if options.get('outlet') or options.get('device') or options.get('order'):
                raise CommandError('Use --list separately from --outlet, --device, and --order.')
            self.stdout.write('DEVICE  USER  OUTLET  ACTIVE  AUTHORIZED  LAST REGISTERED')
            count = 0
            for device in devices.order_by('pk').iterator():
                authorized = can_access(device.user, device.branch, 'read')
                self.stdout.write(f'{device.pk}  {device.user_id}  {device.branch_id}  {device.active}  {authorized}  {device.updated_at.isoformat()}')
                count += 1
            if not count:
                self.stdout.write('No registered phones. Sign in on the mobile app and allow registration to finish.')
            return

        outlet_id = options.get('outlet')
        device_id = options.get('device')
        order_id = options.get('order')
        dry_run = options.get('dry_run', False)

        if not outlet_id and not device_id:
            raise CommandError('Provide --outlet ID (e.g. python manage.py test_order_push --outlet 1), --device ID, or --list.')

        target_devices = []
        if outlet_id:
            outlet_devices = list(devices.filter(branch_id=outlet_id, active=True))
            if not outlet_devices:
                self.stdout.write(self.style.WARNING(f'No active mobile devices currently logged into outlet {outlet_id}.'))
                self.stdout.write('Log in to the mobile app for this outlet first.')
                return

            for dev in outlet_devices:
                if not dev.branch.is_active or not dev.branch.restaurant.is_active or not can_access(dev.user, dev.branch, 'read'):
                    self.stdout.write(self.style.WARNING(f'Device {dev.pk} (User {dev.user_id}) is registered for outlet {outlet_id} but lacks active read permission.'))
                    continue
                target_devices.append(dev)

            if not target_devices:
                self.stdout.write(self.style.WARNING(f'No active, authorized devices found logged into outlet {outlet_id}.'))
                return
        else:
            dev = devices.filter(pk=device_id).first()
            if not dev or not dev.active:
                raise CommandError('Device does not exist or is inactive.')
            if not dev.branch.is_active or not dev.branch.restaurant.is_active or not can_access(dev.user, dev.branch, 'read'):
                raise CommandError('Device user no longer has access to an active outlet.')
            target_devices.append(dev)
            outlet_id = dev.branch_id

        # Determine the order for the test alert
        order = None
        if order_id:
            order = Order.objects.filter(pk=order_id, branch_id=outlet_id).first()
            if not order:
                raise CommandError(f'Order {order_id} does not exist in outlet {outlet_id}.')
        else:
            order = Order.objects.filter(branch_id=outlet_id).order_by('-pk').first()

        if order:
            push_order_id = order.pk
            push_order_number = f'TEST · {order.order_number}'
        else:
            push_order_id = 1
            push_order_number = 'TEST · DUMMY-01'

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Sending test alert for Outlet {outlet_id} (Order: {push_order_number}, ID: {push_order_id}) to {len(target_devices)} logged-in device(s)...'
        ))

        success_count = 0
        for dev in target_devices:
            event_id = uuid.uuid4()
            if dry_run:
                self.stdout.write(self.style.SUCCESS(
                    f'[DRY-RUN] Device {dev.pk} | User {dev.user_id} | Outlet {dev.branch_id} | Token: {dev.token[:12]}... -> Validated successfully.'
                ))
                success_count += 1
                continue

            try:
                message_id = send_new_order_push(
                    dev.token,
                    event_id,
                    push_order_id,
                    push_order_number,
                    dev.branch_id,
                    dev.user_id,
                )
                self.stdout.write(self.style.SUCCESS(
                    f'Firebase accepted: {message_id} (Device {dev.pk}, User {dev.user_id}, Outlet {dev.branch_id})'
                ))
                success_count += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f'FCM send failed for Device {dev.pk} ({type(exc).__name__}): {exc}'
                ))

        if not dry_run and success_count > 0:
            self.stdout.write(self.style.SUCCESS('Push alert sent successfully. Check the registered phone(s).'))
