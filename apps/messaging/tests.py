import uuid
from unittest.mock import patch
from datetime import timedelta
from django.core import signing
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.restaurants.models import Branch, Restaurant
from apps.user_accounts.models import User
from apps.orders.models import MobilePushDevice
from .models import Conversation, Message, ChatEvent, ChatPushDelivery
from .tasks import deliver_chat_events, deliver_chat_push

SETTINGS = dict(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}, CHAT_GUEST_OUTLET_ID=1)

class ChatFixtures:
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create(username='chat-owner', role='RESTAURANT_OWNER')
        self.brand = Restaurant.objects.create(name='Chat brand', admin=self.owner)
        self.branch = Branch.objects.create(pk=1, name='Main', branch_code='CHAT1', restaurant=self.brand)
        self.other = Branch.objects.create(name='Other', branch_code='CHAT2', restaurant=self.brand)
        self.staff = User.objects.create(username='chat-staff', role='BRANCH_MANAGER', restaurant=self.brand, branch=self.branch)
        self.other_staff = User.objects.create(username='chat-other', role='BRANCH_MANAGER', restaurant=self.brand, branch=self.other)
        self.customer = User.objects.create(username='chat-customer', role='CUSTOMER', branch=self.other, restaurant=self.brand)
        self.guest = APIClient(); self.guest.credentials(HTTP_X_CHAT_GUEST='a'*64)
        self.client = APIClient(); self.client.force_authenticate(self.staff)
    def start(self):
        response = self.guest.post('/api/v1/chat/start/', {'outlet_id': self.other.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['conversation']['id']
    def send(self, thread, text='Can you help with my order?', key=None, client=None, staff=False):
        suffix = 'staff/' if staff else ''
        return (client or self.guest).post(f'/api/v1/chat/{suffix}conversations/{thread}/messages/?outlet_id=1',
            {'text': text, 'client_id': str(key or uuid.uuid4())}, format='json')


@override_settings(**SETTINGS)
class ChatTests(ChatFixtures, TestCase):
    def test_guest_is_pinned_to_default_and_reuses_private_thread(self):
        thread = self.start()
        self.assertEqual(self.start(), thread)
        self.assertEqual(Conversation.objects.get(pk=thread).branch_id, 1)
        stranger = APIClient(); stranger.credentials(HTTP_X_CHAT_GUEST='b'*64)
        response = stranger.get(f'/api/v1/chat/conversations/{thread}/messages/')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(APIClient().get('/api/v1/chat/staff/').status_code, 401)
        self.assertIn(APIClient().post('/api/v1/chat/staff/socket-ticket/', {}).status_code, (401,403))

    def test_customer_uses_assigned_branch_not_requested_outlet(self):
        self.guest.force_authenticate(self.customer)
        response = self.guest.post('/api/v1/chat/start/', {'outlet_id':1}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['conversation']['outlet_id'], self.other.pk)
        thread = response.data['conversation']['id']
        self.assertEqual(self.client.get(f'/api/v1/chat/staff/conversations/{thread}/messages/?outlet_id=1').status_code, 403)

    def test_login_does_not_expose_guest_or_other_customer_history(self):
        thread = self.start()
        self.guest.force_authenticate(self.customer)
        self.assertEqual(self.guest.get(f'/api/v1/chat/conversations/{thread}/messages/').status_code,403)

    def test_idempotent_message_and_outbox_are_atomic(self):
        thread = self.start(); key = uuid.uuid4()
        a = self.send(thread, key=key); b = self.send(thread, key=key)
        self.assertEqual(a.status_code,201,a.data); self.assertEqual(a.data,b.data)
        self.assertEqual(Message.objects.count(),1); self.assertEqual(ChatEvent.objects.count(),1)
        self.assertEqual(self.send(thread,text='Different body',key=key).status_code,400)
        self.assertEqual(self.send(thread,text='  ').status_code,400)
        self.assertEqual(self.send(thread,text='x'*2001).status_code,400)

    def test_staff_scope_and_read_receipts(self):
        thread = self.start(); sent = self.send(thread)
        inbox = self.client.get('/api/v1/chat/staff/?outlet_id=1')
        self.assertEqual(inbox.status_code,200,inbox.data); self.assertEqual(inbox.data['unread_count'],1)
        self.client.post(f'/api/v1/chat/staff/conversations/{thread}/read/?outlet_id=1', {'last_message_id':sent.data['id']},format='json')
        self.assertEqual(self.client.get('/api/v1/chat/staff/?outlet_id=1').data['unread_count'],0)
        self.client.force_authenticate(self.other_staff)
        self.assertEqual(self.client.get('/api/v1/chat/staff/?outlet_id=1').status_code,403)
        self.assertEqual(self.send(thread,client=self.client,staff=True).status_code,403)

    def test_staff_reply_is_unread_for_customer_then_read(self):
        thread = self.start()
        reply = self.send(thread,'Happy to help',client=self.client,staff=True)
        self.assertEqual(reply.status_code,201,reply.data)
        data = self.guest.get(f'/api/v1/chat/conversations/{thread}/messages/').data
        self.assertEqual(data['conversation']['unread_count'],1)
        self.guest.post(f'/api/v1/chat/conversations/{thread}/read/',{'last_message_id':reply.data['id']},format='json')
        self.assertEqual(self.guest.get(f'/api/v1/chat/conversations/{thread}/messages/').data['conversation']['unread_count'],0)

    def test_history_pages_have_no_gaps(self):
        thread = Conversation.objects.get(pk=self.start())
        Message.objects.bulk_create([Message(conversation=thread, sender_key='guest', client_id=uuid.uuid4(), text=str(i)) for i in range(65)])
        latest = self.guest.get(f'/api/v1/chat/conversations/{thread.pk}/messages/').data
        self.assertEqual(len(latest['messages']),50);self.assertTrue(latest['has_more'])
        older = self.guest.get(f'/api/v1/chat/conversations/{thread.pk}/messages/?before={latest["messages"][0]["id"]}').data
        self.assertEqual(len(older['messages']),15);self.assertFalse(older['has_more'])
        self.assertEqual(self.guest.get(f'/api/v1/chat/conversations/{thread.pk}/messages/?before=bad').status_code,400)

    def test_reconnect_fetches_every_missed_message_in_sequence(self):
        thread = Conversation.objects.get(pk=self.start())
        first = self.send(str(thread.pk)).data['id']
        Message.objects.bulk_create([Message(conversation=thread, sender_key='guest', client_id=uuid.uuid4(), text=str(i)) for i in range(135)])
        page = self.guest.get(f'/api/v1/chat/conversations/{thread.pk}/messages/?after={first}').data
        self.assertEqual(len(page['messages']), 100); self.assertTrue(page['has_newer'])
        next_page = self.guest.get(f'/api/v1/chat/conversations/{thread.pk}/messages/?after={page["messages"][-1]["id"]}').data
        self.assertEqual(len(next_page['messages']), 35); self.assertFalse(next_page['has_newer'])
        self.assertEqual(len({m['id'] for m in page['messages']+next_page['messages']}), 135)
        initial = self.guest.get(f'/api/v1/chat/conversations/{thread.pk}/messages/?after=0').data
        self.assertEqual(initial['messages'][0]['id'], first)
        self.assertTrue(initial['has_newer'])

    def test_guest_credential_and_socket_ticket_are_required(self):
        self.assertEqual(APIClient().post('/api/v1/chat/start/',{}).status_code,400)
        thread = self.start()
        response = self.guest.post('/api/v1/chat/socket-ticket/', {'conversation_id':thread},format='json')
        data = signing.loads(response.data['ticket'],salt='chat-socket',max_age=60)
        self.assertEqual(data['conversation_id'],thread);self.assertFalse(data['staff'])
        self.assertNotIn('guest_hash', self.guest.get(f'/api/v1/chat/conversations/{thread}/messages/').data['conversation'])

    def test_push_is_one_per_message_device_and_only_customer_messages(self):
        thread = self.start()
        MobilePushDevice.objects.create(user=self.staff,branch=self.branch,token='test-token',platform='android',active=True)
        MobilePushDevice.objects.create(user=self.other_staff,branch=self.branch,token='unauthorized',platform='android',active=True)
        self.send(thread);self.send(thread,'Second');self.send(thread,'Reply',client=self.client,staff=True)
        deliver_chat_events();deliver_chat_events()
        self.assertEqual(ChatPushDelivery.objects.count(),2)
        with patch('apps.messaging.tasks.send_chat_push') as send:
            deliver_chat_push();deliver_chat_push()
        self.assertEqual(send.call_count,2)
        self.assertEqual(ChatPushDelivery.objects.filter(completed_at__isnull=False,last_error='').count(),2)

    def test_push_retry_and_device_reassignment(self):
        thread = self.start()
        device = MobilePushDevice.objects.create(user=self.staff,branch=self.branch,token='test-token',platform='android',active=True)
        self.send(thread);deliver_chat_events()
        with patch('apps.messaging.tasks.send_chat_push',side_effect=RuntimeError('temporary')), patch('apps.messaging.tasks.is_unregistered_device_error',return_value=False):
            deliver_chat_push()
        row=ChatPushDelivery.objects.get();self.assertEqual(row.attempts,1);self.assertIsNone(row.completed_at)
        device.user=self.other_staff;device.save()
        row.next_attempt_at=timezone.now();row.save()
        with patch('apps.messaging.tasks.send_chat_push') as send: deliver_chat_push()
        send.assert_not_called();row.refresh_from_db();self.assertIsNotNone(row.completed_at)

    def test_disabled_restaurant_does_not_receive_push(self):
        thread = self.start()
        MobilePushDevice.objects.create(user=self.staff, branch=self.branch, token='test-token', platform='android', active=True)
        self.send(thread); deliver_chat_events()
        self.brand.is_active = False; self.brand.save()
        with patch('apps.messaging.tasks.send_chat_push') as send: deliver_chat_push()
        send.assert_not_called()
        self.assertIsNotNone(ChatPushDelivery.objects.get().completed_at)

    def test_socket_failure_does_not_lose_push_or_message(self):
        thread=self.start();MobilePushDevice.objects.create(user=self.staff,branch=self.branch,token='t',platform='android',active=True)
        self.send(thread)
        with patch('apps.messaging.tasks.get_channel_layer',side_effect=RuntimeError('redis unavailable')): deliver_chat_events()
        event=ChatEvent.objects.get();self.assertIsNone(event.published_at);self.assertEqual(ChatPushDelivery.objects.count(),1)
        event.next_attempt_at=timezone.now();event.save();deliver_chat_events();event.refresh_from_db()
        self.assertIsNotNone(event.published_at)


@override_settings(**SETTINGS)
class ChatSocketTests(ChatFixtures, TransactionTestCase):
    def test_socket_private_group_and_revocation(self):
        from asgiref.sync import async_to_sync
        from channels.testing import WebsocketCommunicator
        from channels.routing import URLRouter
        from channels.layers import get_channel_layer
        from channels.db import database_sync_to_async
        from .routing import websocket_urlpatterns
        thread=self.start()
        ticket=self.guest.post('/api/v1/chat/socket-ticket/',{'conversation_id':thread},format='json').data['ticket']
        async def run():
            socket=WebsocketCommunicator(URLRouter(websocket_urlpatterns),f'/ws/chat/?ticket={ticket}')
            connected,_=await socket.connect();self.assertTrue(connected)
            self.assertEqual((await socket.receive_json_from())['event_type'],'CONNECTED')
            await get_channel_layer().group_send('chat_staff_1',{'type':'chat_event','event_type':'PRIVATE'})
            self.assertTrue(await socket.receive_nothing(timeout=.05))
            await socket.send_json_to({'type':'ping'})
            self.assertEqual((await socket.receive_json_from())['event_type'],'HEARTBEAT')
            await database_sync_to_async(Branch.objects.filter(pk=1).update)(is_active=False)
            await socket.send_json_to({'type':'ping'})
            self.assertEqual((await socket.receive_output())['code'],4403)
            await socket.disconnect()
            invalid=WebsocketCommunicator(URLRouter(websocket_urlpatterns),'/ws/chat/?ticket=invalid')
            self.assertFalse((await invalid.connect())[0]);await invalid.disconnect()
        async_to_sync(run)()
