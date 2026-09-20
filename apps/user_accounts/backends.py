from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from .selectors import get_user_by_identifier

User = get_user_model()


class MultiIdentifierAuthBackend(ModelBackend):
    """
    Authentication backend that permits users to authenticate
    using either their Phone Number, Email address, or Username.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        # In Django's auth system, 'username' parameter holds the primary identifier passed by forms/API
        identifier = username or kwargs.get('email') or kwargs.get('phone_number') or kwargs.get('identifier')
        if not identifier or not password:
            return None

        user = get_user_by_identifier(identifier)
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

