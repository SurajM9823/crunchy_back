from django.conf import settings
from datetime import timedelta


def send_new_order_push(token, event_id, order_id, order_number, outlet_id, user_id):
    import firebase_admin
    from firebase_admin import credentials, messaging

    project_id = settings.FIREBASE_PROJECT_ID
    if not project_id:
        raise RuntimeError('FIREBASE_PROJECT_ID must be configured to send mobile order alerts.')

    try:
        app = firebase_admin.get_app()
    except ValueError:
        import os
        cred_path = getattr(settings, 'FIREBASE_CREDENTIALS_PATH', '') or os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '')
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            cred = credentials.ApplicationDefault()
        app = firebase_admin.initialize_app(
            cred,
            options={'projectId': project_id},
        )

    message = messaging.Message(
        token=token,
        data={
            'type': 'NEW_ORDER',
            'event_id': str(event_id),
            'order_id': str(order_id),
            'order_number': str(order_number),
            'outlet_id': str(outlet_id),
            'user_id': str(user_id),
        },
        android=messaging.AndroidConfig(priority='high', ttl=timedelta(hours=1)),
    )
    return messaging.send(message, app=app)


def is_unregistered_device_error(error):
    from firebase_admin import messaging
    if isinstance(error, (messaging.UnregisteredError, messaging.SenderIdMismatchError)):
        return True
    code = getattr(error, 'code', '')
    if callable(code):
        code = code()
    return str(code).upper().replace('-', '_').endswith(('UNREGISTERED', 'SENDER_ID_MISMATCH'))


def send_chat_push(token, event_id, conversation_id, message_id, outlet_id, user_id, name, text):
    import firebase_admin
    from firebase_admin import credentials, messaging
    import os
    if not settings.FIREBASE_PROJECT_ID:
        raise RuntimeError('FIREBASE_PROJECT_ID must be configured.')
    try:
        app = firebase_admin.get_app()
    except ValueError:
        path = getattr(settings, 'FIREBASE_CREDENTIALS_PATH', '') or os.getenv('GOOGLE_APPLICATION_CREDENTIALS', '')
        credential = credentials.Certificate(path) if path and os.path.exists(path) else credentials.ApplicationDefault()
        app = firebase_admin.initialize_app(credential, options={'projectId': settings.FIREBASE_PROJECT_ID})
    return messaging.send(messaging.Message(token=token, data={
        'type': 'CHAT_MESSAGE', 'event_id': str(event_id), 'conversation_id': str(conversation_id),
        'message_id': str(message_id), 'outlet_id': str(outlet_id), 'user_id': str(user_id),
        'customer_name': name[:80], 'preview': text[:160],
    }, android=messaging.AndroidConfig(priority='high', ttl=timedelta(hours=1))), app=app)
