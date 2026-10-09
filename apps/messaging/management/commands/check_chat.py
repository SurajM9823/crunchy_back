import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.cache import cache
from apps.restaurants.models import Branch
from apps.orders.models import MobilePushDevice
from apps.messaging.models import ChatEvent, ChatPushDelivery

class Command(BaseCommand):
    help = 'Check chat deployment configuration without sending any messages or printing secrets.'
    def handle(self, *args, **options):
        issues = []
        backend = settings.CHANNEL_LAYERS['default']['BACKEND']
        self.stdout.write(f'Channel backend: {backend}')
        if 'redis' not in backend.lower(): issues.append('Use CHANNEL_LAYER_TYPE=redis in production; in-memory channels cannot connect separate workers.')
        try:
            cache.set('chat:healthcheck', 'ok', 10)
            if cache.get('chat:healthcheck') != 'ok': issues.append('Cache read/write check failed.')
        except Exception as error: issues.append(f'Cache unavailable ({type(error).__name__}).')
        if not settings.FIREBASE_PROJECT_ID: issues.append('FIREBASE_PROJECT_ID is missing.')
        path = settings.FIREBASE_CREDENTIALS_PATH or os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '')
        if path and not os.path.isfile(path): issues.append('Firebase credential file is unavailable to this process.')
        if not path: self.stdout.write('Firebase credentials: using Application Default Credentials; verify the service identity on this server.')
        if not Branch.objects.filter(pk=settings.CHAT_GUEST_OUTLET_ID, is_active=True, restaurant__is_active=True).exists():
            issues.append('The default guest outlet is missing or inactive.')
        self.stdout.write(f'Guest outlet: {settings.CHAT_GUEST_OUTLET_ID}')
        self.stdout.write(f'Active mobile devices: {MobilePushDevice.objects.filter(active=True).count()}')
        self.stdout.write(f'Unpublished chat events: {ChatEvent.objects.filter(published_at__isnull=True).count()}')
        self.stdout.write(f'Pending push deliveries: {ChatPushDelivery.objects.filter(completed_at__isnull=True).count()}')
        self.stdout.write(f'Failed/expired push deliveries: {ChatPushDelivery.objects.filter(completed_at__isnull=False).exclude(last_error="").count()}')
        self.stdout.write('Verify crunchy-daphne, crunchy-celery and one crunchy-celerybeat process are running.')
        if issues: raise CommandError(' '.join(issues))
        self.stdout.write(self.style.SUCCESS('Configuration checks passed. A real phone delivery test is still required.'))
