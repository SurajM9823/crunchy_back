# CRUNCHY BAG — ARCHITECTURE & HIGH-SCALE TECHNICAL SPECIFICATION

**System**: Enterprise Multi-Outlet Restaurant Ordering, POS, Kiosk, Kitchen (KDS), Delivery & Real-Time Operations Platform  
**Scale Target**: 10,000+ concurrent users, 100+ franchise outlets, sub-50ms API response time, zero data loss  
**Tech Stack**: Django 5.1, Django REST Framework, PostgreSQL + PostGIS, Redis 8, Celery 5, Django Channels 4 (Daphne/ASGI), Cloudflare CDN, Docker  

---

## 1. Core Architectural Principle (The Golden Rule)

> **An order is validated, recorded once in PostgreSQL as the transactional source of truth, committed, and then decoupled domain events cause every downstream system to react asynchronously.**

### Anti-Pattern (Fragile & Blocking)
```
User → Django View → Save Order → Deduct Stock → Print Receipt → Send SMS → Notify Kitchen → Update TV → Return Response (SLOW / FAILS EASILY)
```

### Event-Driven Target Architecture (Resilient & Hyper-Scalable)
```
[Client: Web / Mobile / Kiosk / QR / POS]
             │ (HTTPS / REST)
             ▼
      [Nginx / Cloudflare CDN]
             │
             ▼
    [Django Order Engine] (Stateless API)
             │
       ┌─────┴──────────────────┐
       ▼                        ▼
[PostgreSQL + PostGIS]       [Redis]
 (Source of Truth)       (Cache / Locks)
       │
       ▼
[OrderCreated Outbox Event]
       │
 ┌─────┼──────────────┬──────────────┬──────────────┬─────────────┐
 ▼     ▼              ▼              ▼              ▼             ▼
Kitchen Inventory   Printer       Payment      Notifications   Analytics
 (KDS)   (Stock)    (Thermal)    (Webhooks)     (Push/SMS)     (Metrics)
   │
   ▼
[Django Channels WebSocket] ──▶ [Kitchen Displays & Customer Live Screens]
```

---

## 2. High-Scale Engineering Principles (Amazon / Uber / Meta Style)

1. **Stateless Backend Instances**: Django handles compute only. No session or application state is held in server RAM. Any request can hit any worker without affinity.
2. **Database is the Single Source of Truth**: PostgreSQL handles durable ACID data. Redis handles caching, short-lived locks, rate-limiting, and real-time event distribution. Never treat Redis as the permanent order store.
3. **Cache-Aside Pattern with Stampede Protection**:
   - Menu, outlet status, categories, and promotions are cached in Redis.
   - Cache misses fetch from PostgreSQL and write to Redis.
   - Cache stampede is mitigated using Redis distributed mutex locks (single-flight rebuild).
4. **Strict Idempotency Everywhere**:
   - Every mutating request (order creation, payments, inventory reservation) requires an `Idempotency-Key` or `transaction_id`.
   - Re-delivered payment webhooks or double-tapped checkout buttons result in an exact replay of the original response without duplicate side-effects.
5. **Transactional Outbox Pattern**:
   - State mutations and event records are saved within the same PostgreSQL transaction.
   - Background workers poll or receive outbox records and dispatch to Celery/Channels, ensuring zero lost events even if Redis or the broker momentarily disconnects.
6. **Multi-Queue Celery Architecture**:
   - Tasks are split across isolated priority queues (`critical`, `payments`, `orders`, `printing`, `notifications`, `analytics`).
   - If an external SMS gateway or printer goes down, order placement and kitchen displays continue operating without latency degradation.
7. **Zero Polling & Zero Page Reloads (Real-Time Reactive Push)**:
   - Full page reloads (`window.location.reload()`) are strictly forbidden. KDS, TV Status Screens, Kiosks, Cashier POS, and Customer Tracking use persistent WebSockets over Daphne and Redis Channel Layer.
   - All state changes (new order arrival, status moves, item stockouts, price changes) automatically mutate the client UI state reactively without the user refreshing.
   - If a client disconnects, it reconnects with exponential backoff and pulls current state via a lightweight snapshot API before resuming WebSocket streaming — zero user refreshes.
8. **Spatial Routing with PostGIS**:
   - Outlet selection and rider delivery matching use spatial indexes (`ST_DWithin`, `ST_Distance`). Never compute distances over full table scans in Python.
9. **Outlet & Franchise Tenant Isolation**:
   - Every staff member, manager, and order is strictly bound to an `outlet_id` and `restaurant_id`. Backend permission layers reject any cross-outlet data access.

---

## 3. High-Scale Traffic & Infrastructure Layout

```
                         [ INTERNET / CLIENTS ]
                                   │
                                   ▼
                       [ Cloudflare CDN & WAF ]
                         - DDoS Mitigation
                         - SSL/TLS Termination
                         - Edge Caching (Static, Assets, Menu Snapshots)
                         - Global Rate Limiting
                                   │
                                   ▼
                       [ Nginx Reverse Proxy ]
                         - Load Balancing (Round-Robin / Least Connections)
                         - Path Routing (/api -> Gunicorn/Daphne, /ws -> Daphne)
                         - Client Max Body Size & Buffer Limits
                                   │
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
      [ Django Gunicorn Nodes ]             [ Daphne ASGI WebSocket Nodes ]
        - API Endpoints                       - Channels Protocol Router
        - Stateless Workers                   - Real-time Alert Groups
                │                                     │
                └──────────────────┬──────────────────┘
                                   │
       ┌───────────────────────────┼───────────────────────────┐
       ▼                           ▼                           ▼
[ PostgreSQL Primary ]      [ Redis 8 Cluster ]         [ Object Storage ]
  - ACID Transactions         - Cache (Cache-Aside)       - Product Images
  - PostGIS Spatial Index     - Channels Pub/Sub Layer    - Invoice PDFs
  - Read Replicas (Future)    - Celery Broker / Results   - Brand Logos
                              - Distributed Locks & OTP
                                   │
                                   ▼
                        [ Celery Worker Pool ]
                          - critical / payments queue
                          - orders queue
                          - printing queue
                          - notifications queue
```

---

## 4. System Components Breakdown

```
apps/
├── common/             # TimeStampedModel, Outbox Pattern, Base Helpers, Correlation IDs
├── user_accounts/      # Authentication, Multi-Identifier Auth, Roles, JWT
├── restaurants/        # Restaurant Brands (Franchises) & Outlets/Branches
├── tables/             # Dining Tables, Secure Opaque QR Token Generation & Resolver
├── menu/               # Categories, Products, Variants, Addons, Outlets Availability
├── pricing/            # Dynamic Pricing, Time-based Rules, Tax, Discounts
├── combos/             # Combo Meals, Component Expansion & Inventory Mapping
├── carts/              # Ephemeral Cart Validation & Reservation Engine
├── orders/             # Order State Machine, Outbox Events, Timeline, Idempotency
├── payments/           # Gateways (eSewa, Khalti, Fonepay, Card), Idempotent Webhooks
├── kitchen/            # Kitchen Display System (KDS), Station Routing, Prep Times
├── inventory/          # Atomic Ingredient Tracking, Recipes, Low Stock Alerts
├── printers/           # Thermal Printer Queues, Hardware Agent Dispatcher
├── delivery/           # Zones, PostGIS Spatial Matching, Rider Live Tracking
├── notifications/      # Async SMS, Email, Push Notifications, Sound Alerts
└── analytics/          # Real-time KPIs, Order Velocity, Kitchen SLA
```

---

## 5. Order State Machine

```
   [CREATED]
       │
       ▼
[PAYMENT_PENDING] ──(Timeout / Cancel)──▶ [CANCELLED]
       │
  (PaymentVerified)
       │
       ▼
  [CONFIRMED]
       │
  (Chef Accepts)
       │
       ▼
  [PREPARING] ──(Broadcast KDS / TV / Customer)
       │
  (Chef Finishes)
       │
       ▼
    [READY] ────(Broadcast TV / Notification / Printer)
       │
  ┌────┴──────────────────────────────┐
  ▼                                   ▼
[SERVED / PICKED_UP]         [OUT_FOR_DELIVERY]
  (Dine-In / Takeaway)           (Delivery)
  │                                   │
  │                             (Rider Delivers)
  │                                   │
  └─────────────────┬─────────────────┘
                    ▼
               [COMPLETED]
```

---

## 6. Execution Rules for Crunchy Bag Development

1. **No Unwanted Frontend Files**: All UI is decoupled into dedicated Next.js/React frontends. Backend provides pure REST APIs, Django Admin for backoffice superusers, and WebSockets.
2. **Service-Selector Pattern**:
   - `selectors.py`: All database reads. No mutations.
   - `services.py`: All business mutations, atomic transactions, and event dispatches.
   - `serializers.py`: DRF input validation and clean response serialization.
   - `views.py`: Thin controllers that delegate immediately to services/selectors.
3. **Database Constraints Over Application Assumptions**: Foreign keys, unique constraints, check constraints, and indexes must exist at the PostgreSQL level.
4. **All Financials Use `DecimalField`**: Never use float for currency, subtotal, tax, or discounts.

