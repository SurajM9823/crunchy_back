"""Private chat uploads. Only authorized conversation members obtain short-lived links."""
import hashlib
import re
import uuid
from pathlib import Path
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from rest_framework.exceptions import ValidationError

@deconstructible
class ChatStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(file_permissions_mode=0o600, directory_permissions_mode=0o700)
    @property
    def location(self):
        return str(Path(settings.CHAT_UPLOAD_ROOT).resolve())
    def url(self, name):
        raise ValueError('Chat files require an authorized download ticket.')


def upload_path(instance, filename):
    return f'{instance.conversation_id}/{uuid.uuid4().hex}'


def inspect_upload(upload):
    limit = settings.CHAT_MAX_UPLOAD_BYTES
    if not upload.size or upload.size > limit:
        raise ValidationError({'file': f'Choose a non-empty file up to {limit // (1024 * 1024)} MB.'})
    name = re.sub(r'[\x00-\x1f\x7f]', '', upload.name.replace('\\', '/').split('/')[-1])[:180] or 'file'
    digest = hashlib.sha256()
    total = 0
    for chunk in upload.chunks():
        total += len(chunk)
        if total > limit: raise ValidationError({'file': 'File exceeds the upload limit.'})
        digest.update(chunk)
    upload.seek(0); head = upload.read(64); upload.seek(0)
    mime = 'application/octet-stream'
    if head.startswith(b'%PDF-'): mime = 'application/pdf'
    elif head.startswith(b'\x89PNG\r\n\x1a\n'): mime = 'image/png'
    elif head.startswith(b'\xff\xd8\xff'): mime = 'image/jpeg'
    elif head[:6] in (b'GIF87a', b'GIF89a'): mime = 'image/gif'
    elif head[:4] == b'RIFF' and head[8:12] == b'WEBP': mime = 'image/webp'
    elif head[:4] == b'RIFF' and head[8:12] == b'WAVE': mime = 'audio/wav'
    elif head[:4] == b'OggS': mime = 'audio/ogg' if upload.content_type != 'video/ogg' else 'video/ogg'
    elif head[:4] == b'fLaC': mime = 'audio/flac'
    elif head[:3] == b'ID3' or (len(head)>1 and head[0]==255 and head[1]&224==224): mime = 'audio/mpeg'
    elif head[4:8] == b'ftyp': mime = 'audio/mp4' if (upload.content_type or '').startswith('audio/') or name.lower().endswith(('.m4a', '.aac')) else 'video/mp4'
    elif head[:4] == b'\x1aE\xdf\xa3': mime = 'audio/webm' if (upload.content_type or '').startswith('audio/') else 'video/webm'
    # Unknown and active formats (HTML/SVG/scripts) are downloaded, never rendered inline.
    return {'file_name': name, 'file_size': total, 'file_mime': mime, 'file_sha256': digest.hexdigest()}
