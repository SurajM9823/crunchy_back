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
        app = firebase_admin.initialize_app(
            credentials.ApplicationDefault(),
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
