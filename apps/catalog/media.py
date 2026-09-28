import hashlib
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.exceptions import ValidationError


def store_menu_image(upload, restaurant_id):
    if not upload or upload.size > 5 * 1024 * 1024:
        raise ValidationError({'image': 'Choose a JPEG, PNG or WebP image of at most 5 MB.'})
    content = upload.read()
    try:
        with Image.open(BytesIO(content)) as img:
            extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}.get(img.format)
            if not extension or img.width * img.height > 20000000:
                raise ValueError('Unsupported image format or dimensions.')
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError({'image': 'Invalid image or image exceeds 20 megapixels.'}) from exc
    key = f'menu/{restaurant_id}/{hashlib.sha256(content).hexdigest()}.{extension}'
    if not default_storage.exists(key):
        key = default_storage.save(key, ContentFile(content))
    cdn = getattr(settings, 'MENU_MEDIA_BASE_URL', '')
    return f'{cdn.rstrip("/")}/{key}' if cdn else default_storage.url(key)
