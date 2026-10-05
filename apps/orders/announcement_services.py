"""Generate a spoken identifier for hybrid calls, or full calls for older clients."""
import asyncio
import hashlib
import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

LETTERS = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', [
    'ए', 'बी', 'सी', 'डी', 'ई', 'एफ', 'जी', 'एच', 'आई', 'जे', 'के', 'एल', 'एम',
    'एन', 'ओ', 'पी', 'क्यू', 'आर', 'एस', 'टी', 'यू', 'भी', 'डब्ल्यू', 'एक्स', 'वाई', 'जेड',
]))
DIGITS = str.maketrans('0123456789', '०१२३४५६७८९')


def spoken_identifier(value):
    """Keep prefixes, but let the Nepali voice read numbers as whole numbers."""
    value = re.sub(r'^table\s*', '', str(value), flags=re.I)
    value = re.sub(r'[A-Za-z]', lambda match: LETTERS[match[0].upper()] + ' ', value)
    value = re.sub(r'\d+', lambda match: str(int(match[0])).translate(DIGITS), value)
    return re.sub(r'\s+', ' ', value.replace('-', ' ')).strip()


def pickup_token_text(token, round_number=1):
    text = spoken_identifier(token)
    if round_number > 1:
        text += f', राउन्ड {str(round_number).translate(DIGITS)}'
    return text


def pickup_text(token, round_number=1, table=None):
    subject = f'टोकन नम्बर {spoken_identifier(token)}'
    if table:
        subject += f', टेबल {spoken_identifier(table)}'
    if round_number > 1:
        subject += f', राउन्ड {str(round_number).translate(DIGITS)}'
    return f'{subject}। तपाईंको अर्डर तयार छ। कृपया काउन्टरबाट लिनुहोस्। धन्यवाद।'


def audio_identity(text):
    voice = settings.TV_NEPALI_VOICE
    digest = hashlib.sha256(f'v2|{voice}|-3%|{text}'.encode()).hexdigest()
    return f'tv-announcements/{digest}.mp3', f'tv-speech:{digest}'


async def synthesize(text, voice):
    import edge_tts
    chunks = []
    async with asyncio.timeout(25):
        async for chunk in edge_tts.Communicate(text, voice, rate='-3%').stream():
            if chunk['type'] == 'audio':
                chunks.append(chunk['data'])
    audio = b''.join(chunks)
    if len(audio) < 1000:
        raise RuntimeError('Speech service returned no usable audio')
    return audio


def generate_audio(text):
    path, key = audio_identity(text)
    if not default_storage.exists(path):
        audio = asyncio.run(synthesize(text, settings.TV_NEPALI_VOICE))
        saved_path = default_storage.save(path, ContentFile(audio))
        # A concurrent worker may have completed the same deterministic file.
        if saved_path != path:
            default_storage.delete(saved_path)
    cache.delete(key)
    return path


def request_audio(text):
    """Return a stored file or enqueue one bounded, deduplicated background job."""
    path, key = audio_identity(text)
    if default_storage.exists(path):
        return path, 'ready'
    if cache.get(key) == 'failed':
        return None, 'failed'
    if cache.add(key, 'pending', timeout=600):
        from .tasks import generate_pickup_audio
        try:
            generate_pickup_audio.delay(text)
        except Exception:
            cache.delete(key)
            logger.exception('Could not queue Nepali announcement')
            return None, 'failed'
    return None, 'pending'
