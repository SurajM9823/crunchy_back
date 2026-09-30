"""Encrypt recoverable SMS credentials; never return them through serializers."""
import base64
import hashlib
from cryptography.fernet import Fernet
from django.conf import settings


def cipher():
    key = hashlib.sha256(('sparrow-sms:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(value):
    return cipher().encrypt(value.encode()).decode()


def unseal(value):
    return cipher().decrypt(value.encode()).decode()
