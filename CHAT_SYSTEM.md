# Customer and staff messaging

The website floating chat button supports guests and authenticated customers. The mobile dashboard message icon opens the staff inbox. Staff can also reply from the website chat inbox. Text is plain text (up to 2,000 characters), not executable HTML. Messages help customers discuss orders; they do not automatically create or change orders.

## Routing and privacy

- Guests always start at CHAT_GUEST_OUTLET_ID (default 1), regardless of a browser-supplied outlet ID.
- Signed-in accounts with an assigned branch use that branch. Customers without a fixed branch use the selected authenticated storefront outlet, validated against their account restaurant when present.
- Staff require the existing orders permission for the requested outlet. Every REST request and socket event checks access. Socket tickets expire after 60 seconds for connection, and connections renew after five minutes.
- Guest history is protected by a browser-generated 256-bit bearer credential; only its hash is stored in the database. Guest chat is browser-specific and intentionally does not silently transfer to a different signed-in identity. Clearing browser storage loses guest access.
- Messages and read receipts are stored in PostgreSQL. Redis is transport/cache, not permanent history. Public responses are private/no-store. Website chat is excluded from PostHog capture with the sensitive/no-capture classes.
- The API applies a per-user/IP rate limit. Run behind the configured trusted reverse proxy and retain edge request limits for anonymous traffic.

## Reliability

Each send has a client UUID and a unique conversation/sender/client constraint. Retrying an uncertain request returns the same saved message. Pending sends survive browser reload in session storage or mobile process restarts in app-private storage. WebSocket reconnect always obtains a new signed ticket and fetches missed messages using an after cursor. History and inbox have older-page controls.

The atomic chat outbox feeds two Celery beat jobs every two seconds. Customer messages create one push delivery per authorised active staff device. Staff replies do not send a staff push. WebSocket outages do not prevent push records from being committed. Firebase failures retry up to four attempts with exponential backoff; alerts expire after an hour. Device reassignment and revoked access are checked again before sending. Each event has a stable mobile notification ID and is deduplicated locally.

FCM uses high-priority data messages. The mobile handler immediately shows a customer-message notification without a network request. Notification taps open the conversation. Android permission, notification channel settings, connectivity and device restrictions still apply; this is not a guarantee of delivery when force-stopped. See https://firebase.google.com/docs/cloud-messaging/android-message-priority and https://developer.android.com/social-and-messaging/guides/communication/receiving-messages .

## Deploy

Deploy the backend before the website and APK. Existing order push registration is reused; no new Firebase project or paid messaging provider is needed.

1. Include apps/messaging and its 0001_initial migration in the deployment. Check that the newly added source files and migration are included in the deployment.
2. Set CHANNEL_LAYER_TYPE=redis, REDIS_URL and the existing Celery broker configuration on the server. Ensure FIREBASE_PROJECT_ID and server-only FIREBASE_CREDENTIALS_PATH (or Application Default Credentials) are configured for the worker. CHAT_GUEST_OUTLET_ID defaults to 1.
3. Run:

```bash
cd /opt/crunchy_back
.venv/bin/python manage.py migrate --noinput
sudo systemctl restart crunchy-daphne crunchy-celery crunchy-celerybeat
.venv/bin/python manage.py check_chat
```

4. The existing Nginx /ws/ upgrade proxy already covers /ws/chat/. The existing services and Celery task autodiscovery support apps.messaging without a new service. Run only one beat scheduler.
5. Build/deploy the website normally (`npm run build`) and install the new mobile APK. The mobile app must be signed in to its outlet, registered for push, and have Customer messages notifications enabled.

The developer workstation currently uses in-memory channels and has no configured Firebase project/credential environment values. That is sufficient for isolated tests but not cross-process live delivery. No server deployment or live Firebase delivery is claimed by local test results.

## Verify after deployment

- Open a guest browser, send two messages, and verify the outlet 1 staff inbox receives both once.
- Reply from mobile and verify the browser updates without refresh, unread badges clear when viewed, and read indicators appear.
- Sign into a customer assigned to another outlet and verify outlet 1 staff cannot access that conversation.
- Background/lock the phone, then send another message. Verify its notification opens the correct thread. Repeat with the app removed from Recents. Force-stop from system settings is a different Android state and requires reopening.
- Disconnect either client; send messages from the other; reconnect and verify all missed messages appear once.
- Retry an uncertain send after reopening the app/browser and verify there is no duplicate.
- Confirm disabled devices and logged-out/other-outlet users receive no message alerts.

Automated checks:

```bash
python manage.py test apps.messaging --keepdb
# website
npm run lint
npx playwright test tests/chat.spec.ts
# Android (using the installed Gradle distribution)
gradle :app:assembleDebug :app:testDebugUnitTest
```

## API

All paths start /api/v1/chat/. Guests include X-Chat-Guest; signed-in users use existing authentication.

- POST start/ -> conversation and latest history
- GET/POST conversations/<uuid>/messages/ -> history or send {client_id,text}
- POST conversations/<uuid>/read/ -> {last_message_id}
- POST socket-ticket/ -> {conversation_id} returns signed ticket and socket path
- GET staff/?outlet_id=N -> inbox, unread total, pagination
- Staff message/read/ticket endpoints use staff/ prefix and outlet_id=N
- GET history supports before=<message ID> for older pages or after=<message ID> for reconnect catch-up
- WebSocket /ws/chat/?ticket=... emits CHAT_MESSAGE, CHAT_READ and heartbeat revisions. Message content is fetched over the authorised REST endpoints.


## Conversation identity and typing refinement

Run `python manage.py migrate` for messaging migration 0002, then restart Daphne, Celery worker and beat, deploy the website build, and rebuild/install the Android app.

Guests retain one private thread per browser credential and outlet, with a stable `Guest <id>` label. IP is staff-only context, never a credential: shared Wi-Fi users must not see one another's messages, and changing networks must not lose replies. Signed-in customers retain account/outlet threads. Existing guest labels are derived when read, without rewriting history.

`last_client_ip` updates on customer start/send. `CHAT_TRUSTED_PROXY_CIDRS` defaults to loopback only. Only trusted proxy connections may supply Nginx's overwritten `X-Real-IP`. If behind Cloudflare, configure Nginx real_ip with Cloudflare's trusted ranges first; otherwise this field may show the proxy IP. Do not trust arbitrary forwarding headers or expose Daphne directly with an overly broad proxy allowlist.

WebSocket `{type: "typing", conversation_id, is_typing}` produces ephemeral `CHAT_TYPING` with conversation_id, is_staff and is_typing. Outlet and conversation access are checked on the server. Typing does not create messages or push notifications. Clients throttle updates, stop after inactivity, and expire remote indicators after five seconds. Each customer message continues using the existing durable push outbox.
