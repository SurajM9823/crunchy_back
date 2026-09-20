# CRUNCHY BAG — DEVELOPMENT ROADMAP

A step-by-step implementation sequence designed for production resilience and high scale (10,000+ concurrent users).

---

## Phase Breakdown

### [ACTIVE] Phase 1: Foundation & Authentication ✅
- [x] Django 5.1 & Daphne ASGI initialization
- [x] Environment configuration via `.env`
- [x] Custom User model with Multi-Identifier Authentication (Phone, Email, Username)
- [x] Custom Authentication Backend
- [x] Redis Cache, Celery, and Channels base setup
- [x] Initial Superuser provisioning (`python manage.py setup_initial_superuser`)

---

### [NEXT STEP] Phase 2: Restaurant Brands (Franchises) & Outlets / Admins
*Target: Allow Superusers to create Restaurant Brands, assign/create Restaurant Admins, and provision Branch Outlets via Django Default Admin, backed by robust services and REST APIs.*
- [ ] Install & configure `psycopg2-binary` for PostgreSQL.
- [ ] Create `apps.restaurants`:
  - `Restaurant` (Parent Brand / Franchise: unique name, slug, owner/admin, logo, status).
  - `Branch` (Outlet: unique branch code, location, manager, parent brand FK).
- [ ] Update `User` model with optional foreign keys to `restaurant` and `branch`.
- [ ] Django Default Admin configuration (`admin.py`):
  - Inlines for Branch creation directly on Restaurant Brand admin.
  - Restaurant Admin user creation shortcut.
  - Search, filters, and list views.
- [ ] `selectors.py` & `services.py` for Brands and Outlets.
- [ ] DRF Serializers & API endpoints for external client integration.
- [ ] Automated tests for Brand and Branch creation.

---

### Phase 3: Menus, Categories, Variants & Redis Cache-Aside
- [ ] Category, Product, Variant, and Addon models.
- [ ] Outlet-level menu availability override.
- [ ] Redis cache-aside implementation (`menu:outlet:<id>:v<version>`).
- [ ] Invalidation hooks on menu mutation.

---

### Phase 4: Dining Tables & Secure Opaque QR Token Engine
- [ ] Dining Table models linked to Outlets.
- [ ] Opaque cryptographic QR token generation (zero sensitive data in QR URLs).
- [ ] QR Resolver API: maps QR token to verified Ordering Context (`outlet_id`, `table_id`).

---

### Phase 5: Centralized Order Engine & Transactional Outbox
- [ ] Unified Order model for all channels (TABLE_QR, KIOSK, POS, WEB, DELIVERY).
- [ ] `OrderStatusHistory` immutable audit timeline.
- [ ] `OutboxEvent` table and dispatcher for zero-data-loss event publishing.
- [ ] Order state transition service with atomic DB locks.

---

### Phase 6: Real-Time KDS (Kitchen Display System) & TV WebSockets
- [ ] Kitchen order routing by outlet and station.
- [ ] Django Channels WebSocket consumer: `/ws/outlets/{id}/kitchen/`.
- [ ] Live TV display WebSocket: `/ws/outlets/{id}/display/`.
- [ ] State synchronization and reconnection protocol.

---

### Phase 7: Atomic Inventory & Recipe Engine
- [ ] Raw ingredients and recipe expansion for items and combos.
- [ ] Atomic inventory deduction (`UPDATE inventory SET stock = stock - X WHERE stock >= X`).
- [ ] Out-of-stock event triggers.

---

### Phase 8: Payment Gateway Integrations & Idempotent Webhooks
- [ ] Multi-gateway architecture (eSewa, Khalti, Fonepay, Card).
- [ ] Idempotent webhook receiver with signature validation.
- [ ] Automatic transition to `CONFIRMED` and trigger of kitchen events.

---

### Phase 9: PostGIS Delivery Zones & Rider Geo-Tracking
- [ ] PostGIS spatial queries for nearest eligible outlet.
- [ ] Delivery zone polygons.
- [ ] Ephemeral rider GPS tracking in Redis with live customer map WebSockets.

---

### Phase 10: Thermal Printing, Kiosks, Observability & Cloudflare Hardening
- [ ] Celery thermal print queues with offline agent fallback.
- [ ] Terminal heartbeat monitor.
- [ ] Prometheus metrics, Sentry error tracking, and Cloudflare CDN caching rules.

