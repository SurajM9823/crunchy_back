# CRUNCHY BAG — ENGINEERING RULES & CODING STANDARDS

The following rules are mandatory across all modules and code written for the Crunchy Bag platform:

---

## 1. The 15 Golden Rules

| # | Rule | Standard |
|---|---|---|
| **Rule 1** | **Never block HTTP requests** | Any work taking > 50ms (SMS, Push, Email, Printing, Analytics) MUST be dispatched to Celery background workers. |
| **Rule 2** | **Never trust client-side data** | Prices, discounts, totals, availability, and taxes are recalculated exclusively on the backend. |
| **Rule 3** | **Zero page reloads — Real-time auto-update** | NEVER require or trigger full page reloads to see new data. Every screen (KDS, TV, POS, Kiosk, Table QR, Customer Tracking, and Admin Dashboards) MUST automatically update live state via Django Channels WebSockets over Redis. |
| **Rule 4** | **PostgreSQL is the single source of truth** | Redis is purely for speed, locks, cache, and transport. Never store permanent business state in Redis. |
| **Rule 5** | **Idempotent mutations everywhere** | Payment webhooks, order submissions, and inventory operations must check idempotency keys before processing. |
| **Rule 6** | **All background tasks must support retry** | Use exponential backoff (`default_retry_delay=60`, `max_retries=3`) and record terminal failures. |
| **Rule 7** | **Immutable Order Timeline** | Every transition in the order lifecycle must append a record in `OrderStatusHistory` with timestamps and initiator. |
| **Rule 8** | **Device heartbeats & observability** | Terminals (Kiosks, POS, KDS, Thermal Printers) must emit periodic heartbeats tracked in Redis. |
| **Rule 9** | **Strict tenant and outlet isolation** | Users (cashiers, managers, chefs) are authorized strictly for their assigned `outlet_id`. Cross-tenant queries are blocked. |
| **Rule 10** | **Resilient WebSocket clients** | Clients must implement exponential backoff reconnection and fetch a state snapshot from REST API upon reconnecting (never a full browser refresh). |
| **Rule 11** | **Structured event schema** | Every domain event carries a unique `event_id`, `event_type`, `aggregate_id`, `outlet_id`, and ISO-8601 `timestamp`. |
| **Rule 12** | **Outbox pattern for business events** | Critical events are saved in an `OutboxEvent` table inside the same DB transaction as the aggregate before publication. |
| **Rule 13** | **Cache-aside with single-flight locking** | Never let cache invalidation cause a thundering herd on PostgreSQL. Use Redis locks to single-thread cache rebuilds. |
| **Rule 14** | **Modular monolith architecture** | Keep code organized in cleanly bounded Django apps (`apps/<domain>`) with strict Service-Selector boundaries for future microservice extraction. |
| **Rule 15** | **Stateless compute** | Zero sticky sessions. Any Django worker must be able to serve any request, enabling seamless horizontal autoscaling. |

---

## 2. Directory & Coding Conventions

Every app under `apps/` must strictly follow this structure:

```
apps/<app_name>/
├── __init__.py
├── apps.py
├── models.py       # Database schema, foreign keys, db_index, constraints
├── selectors.py    # PURE read queries (returns QuerySets or model instances)
├── services.py     # PURE write business operations (atomic transactions, validation, events)
├── serializers.py  # DRF serializers (input validation & API responses)
├── views.py        # Thin API controllers (DRF APIViews calling services/selectors)
├── urls.py         # URL routing
├── admin.py        # Custom Django Admin registrations
├── tasks.py        # Celery background tasks (if applicable)
├── consumers.py    # WebSocket consumers (if applicable)
└── routing.py      # WebSocket routing (if applicable)
```

- **NO Business Logic in Views**: Views should not exceed 25-30 lines. They parse request data, call serializers, invoke a service or selector, and return a standard response.
- **NO Database Writes in Selectors**: Functions in `selectors.py` must never call `.save()`, `.delete()`, `.create()`, or `.update()`.
- **NO Frontend Templates**: All client applications (Customer Web, Kiosk, POS, KDS) are separate frontend applications communicating via REST and WebSockets. Backend only uses Django's default Admin portal for superusers.

---

## 3. The Zero Page Reload & Real-Time Auto-Update Architecture

In high-scale platforms (like WhatsApp, Uber, and Amazon Live), the user **never** has to press refresh:

```
[ Backend Event ] (e.g., OrderConfirmed, ItemSoldOut, ChefStarted, OrderReady)
        │
        ▼
[ Django Channels Broadcast via Redis Pub/Sub ]
        │
        ▼
[ Persistent WebSocket Connection ] (ws://.../ws/...)
        │
        ▼
[ Client React / State Engine ] (Auto-updates UI state instantly without reloading)
```

### Specific Real-Time Auto-Update Mandates:
1. **Kitchen Display System (KDS)**:
   - When a new order arrives, it immediately slides into the active kitchen ticket queue with an audio alert. No manual F5 refresh.
2. **Live TV / Customer Order Display**:
   - When a chef marks an order from `PREPARING` to `READY`, the order number instantly animates into the "READY FOR PICKUP" column on the TV screen.
3. **Customer Live Order Tracking**:
   - Order progress steps (`CONFIRMED` ➔ `PREPARING` ➔ `READY` ➔ `OUT_FOR_DELIVERY` ➔ `COMPLETED`) auto-advance in real time on the customer's phone/screen.
4. **Live Menu & Stock Out-of-Stock Toggles**:
   - When a manager or automated inventory marks an item `is_available = False`, connected Kiosks, POS terminals, and customer digital menus instantly disable the item button to prevent race conditions and failed checkouts.
5. **Reconnection & State Re-syncing**:
   - If the device loses internet connection, it enters an auto-reconnect loop with exponential backoff. Upon reconnecting, it queries a lightweight delta endpoint (`GET /api/v1/sync/`) and immediately resumes live streaming without ever re-rendering or reloading the whole page.


