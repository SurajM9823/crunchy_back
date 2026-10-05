import tempfile
from unittest.mock import AsyncMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product
from apps.restaurants.models import Branch, Restaurant
from apps.user_accounts.models import User
from .announcement_services import audio_identity, generate_audio, pickup_text, pickup_token_text, spoken_identifier
from .models import Order, OrderItem, OrderOutboxEvent


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
                   TV_NEPALI_VOICE='ne-NP-HemkalaNeural')
class AnnouncementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = User.objects.create(username='voice-owner')
        brand = Restaurant.objects.create(name='Voice brand', admin=owner)
        cls.branch = Branch.objects.create(name='Voice outlet', branch_code='VOICE', restaurant=brand)
        cls.other = Branch.objects.create(name='Other outlet', branch_code='OTHER', restaurant=brand)
        category = Category.objects.create(name='Food', restaurant=brand)
        product = Product.objects.create(name='Burger', category=category, base_price=100)
        cls.order = Order.objects.create(branch=cls.branch, order_number='POS-21', status='PREPARING')
        OrderItem.objects.create(order=cls.order, product=product, product_name='Burger', unit_price=100, line_total=100)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.directory.name)
        self.media.enable()
        self.addCleanup(self.media.disable)
        cache.clear()
        self.client = APIClient()
        self.url = f'/api/v1/orders/display/{self.branch.pk}/announcement/?token=POS-21&round=1'

    @patch('apps.orders.tasks.generate_pickup_audio.delay')
    def test_request_queues_once_and_reuses_complete_recording(self, delay):
        for _ in range(2):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 202)
        text = pickup_text('POS-21')
        delay.assert_called_once_with(text)
        with patch('apps.orders.announcement_services.synthesize', new=AsyncMock(return_value=b'whole-recording')) as speech:
            generate_audio(text)
            generate_audio(text)
            speech.assert_awaited_once_with(text, 'ne-NP-HemkalaNeural')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'audio/mpeg')
        self.assertEqual(b''.join(response.streaming_content), b'whole-recording')
        response.close()

    @patch('apps.orders.tasks.generate_pickup_audio.delay')
    def test_other_outlet_missing_round_and_free_text_cannot_generate(self, delay):
        self.assertEqual(self.client.get(self.url.replace(f'/{self.branch.pk}/', f'/{self.other.pk}/')).status_code, 404)
        self.assertEqual(self.client.get(self.url.replace('round=1', 'round=2')).status_code, 404)
        self.assertEqual(self.client.get(self.url.replace('POS-21', 'arbitrary-speech')).status_code, 404)
        self.assertEqual(self.client.get(self.url.replace('round=1', 'round=-1')).status_code, 400)
        delay.assert_not_called()

    @patch('apps.orders.tasks.generate_pickup_audio.delay', side_effect=RuntimeError('offline'))
    def test_queue_failure_is_retryable(self, delay):
        self.assertEqual(self.client.get(self.url).status_code, 503)
        delay.side_effect = None
        self.assertEqual(self.client.get(self.url).status_code, 202)

    def test_whole_numbers_and_rounds_are_in_one_sentence(self):
        self.assertEqual(spoken_identifier('POS-021'), 'पी ओ एस २१')
        text = pickup_text('POS-21', 2, 'Table 04')
        self.assertIn('टेबल ४, राउन्ड २', text)
        self.assertIn('कृपया काउन्टरबाट लिनुहोस्।', text)
        first, _ = audio_identity(text)
        with override_settings(TV_NEPALI_VOICE='ne-NP-SagarNeural'):
            self.assertNotEqual(first, audio_identity(text)[0])

    @patch('apps.orders.announcement_services.request_audio', side_effect=RuntimeError('speech unavailable'))
    def test_speech_prewarm_failure_does_not_block_order_events(self, request):
        from .tasks import publish_pos_events
        event = OrderOutboxEvent.objects.create(branch=self.branch, order=self.order, event_type='ORDER_TRANSITION', payload={})
        with self.assertLogs('apps.orders.tasks', level='ERROR'):
            publish_pos_events()
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)
        request.assert_called_once_with(pickup_token_text('POS-21'))

    @patch('apps.orders.tasks.generate_pickup_audio.delay')
    def test_hybrid_generates_only_the_identifier(self, delay):
        response = self.client.get(self.url + '&part=token')
        self.assertEqual(response.status_code, 202)
        delay.assert_called_once_with('पी ओ एस २१')
        self.assertEqual(pickup_token_text('POS-21', 2), 'पी ओ एस २१, राउन्ड २')
        self.assertNotEqual(audio_identity(pickup_text('POS-21')), audio_identity(pickup_token_text('POS-21')))

    @patch('apps.orders.announcement_services.generate_audio', side_effect=RuntimeError('speech offline'))
    def test_terminal_generation_failure_is_logged_and_retryable_later(self, generate):
        from .tasks import generate_pickup_audio
        text = pickup_text('POS-21')
        generate_pickup_audio.push_request(retries=3)
        try:
            with self.assertLogs('apps.orders.tasks', level='ERROR'), self.assertRaises(RuntimeError):
                generate_pickup_audio.run(text)
        finally:
            generate_pickup_audio.pop_request()
        self.assertEqual(cache.get(audio_identity(text)[1]), 'failed')
        self.assertEqual(self.client.get(self.url).status_code, 503)
