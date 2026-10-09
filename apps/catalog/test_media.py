import hashlib
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from .media import store_menu_image


class MenuImageUploadTests(SimpleTestCase):
    @override_settings(MENU_MEDIA_BASE_URL='https://cdn.example.com')
    @patch('apps.catalog.media.default_storage.save', side_effect=lambda name, content: name)
    @patch('apps.catalog.media.default_storage.exists', return_value=False)
    def test_uploads_avif_image(self, _exists, _save):
        image_data = BytesIO()
        Image.new('RGB', (1, 1), color=(255, 0, 0)).save(image_data, format='AVIF')
        content = image_data.getvalue()
        upload = SimpleUploadedFile('dish.avif', content, content_type='image/avif')

        url = store_menu_image(upload, 'restaurant-1')

        digest = hashlib.sha256(content).hexdigest()
        self.assertEqual(url, f'https://cdn.example.com/menu/restaurant-1/{digest}.avif')
