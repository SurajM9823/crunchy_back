# Staff POS ordering and billing

Frontend source lives in `C:\Users\Suraj\Desktop\crunchybag`. The temporary backend `.menu-work` directory has been removed. Entry points are `/admin?tab=pos_orders`, the billing tab, and the kitchen tab. Customer website ordering is a separate integration.

## POS screen and floor configuration

The order, ongoing-tab, floor, and billing screens use authenticated server data exclusively. Empty or failed requests never substitute sample orders or tables. The menu uses the POS channel snapshot with outlet prices and availability; create and append commands require a server quotation, `items`, `expected_total`, and the current order version for appends. Failed commands retain the cart.

In **Floor & tables → Manage floors & tables**, create a named group (for example First floor), then add table labels and seat counts. Groups can be renamed, and unoccupied tables can be edited or deactivated. `tables/0002_tablegroup` preserves existing sections as groups without creating sample tables. Configuration commands use `/orders/pos/table-groups/` and `/orders/pos/tables/`, with an optional object ID for edits. They enforce outlet scope, idempotency, duplicate-label checks, and occupied-table protection. Changes publish through the durable menu revision stream so other terminals refresh their metadata.

Vacant table cards start dine-in orders; occupied cards open the existing tab or bill. A completed service releases the table independently of outstanding credit. The billing register includes completed orders with balances, supports server-side filters and pagination, and displays collected payments separately from amounts due. Cash, enabled digital methods, split payments, partial collection, Khata, refunds, and saved receipt reprints use the transactional POS endpoints. A bill alone never marks an order paid.

Startup loads each screen's snapshots once. Socket acknowledgements and unchanged heartbeats do not reload them. Domain events are debounced and deduplicated; reconnection fetches a fresh snapshot. The only repeating POS network timer is the WebSocket heartbeat. Static menus have no timed REST refresh; scheduled menus refresh once at the next configured pricing boundary. A healthy socket renews after five minutes by server policy, so an occasional new socket ticket is expected.

Frontend regression checks: `npm run lint`, `npm run build`, and `npm run test:pos` from the separate frontend repository. Browser tests use Chrome and intercepted API fixtures, including a 95-second virtual idle interval, table order creation, appending, partial/split settlement, cart preservation after a price conflict, and idempotent recovery after an uncertain response. Backend checks: `python manage.py test apps.orders.test_pos apps.catalog --noinput`.

## Data and command flow

- `/api/v1/orders/pos/` is authenticated and outlet-scoped. Existing customer checkout cannot submit a POS order or modify a staff-managed table tab.
- `pos_serializers.py` validates input, `pos_access.py` authorizes staff, `pos_selectors.py` builds read models, and `pos_services.py` owns transactional commands.
- Catalog quotations load the selected products and combo components, rather than every product. The menu snapshot remains cached by outlet, channel, revision and pricing minute. The server checks price again when an order is saved; a changed total produces HTTP 409.
- Normal items, modifiers, variants and combo components retain historical names and prices. Combo components drive kitchen preparation and ingredient consumption; they are not billed a second time as ordinary items.
- Each write requires `Idempotency-Key`. The saved request fingerprint includes the actor, outlet, command, order and validated body. A duplicate returns its original response. Reusing a key for different data returns 409. The frontend retains uncertain requests in session storage and offers recovery.
- Mutations take a short per-outlet database lock, then an order lock. Updates require the current order version. Different outlets write independently. This deliberately favors straightforward consistency over maximum write throughput within one outlet; it needs PostgreSQL load testing before any throughput claim.
- Inventory recipes are consumed once per new order line, under stock row locks, in the same transaction. Insufficient stock rolls back the entire command. Unprepared voids restore their recorded ingredient quantities. Prepared food is not automatically restocked. Both stock ledgers record the operation.
- Order status and settlement are independent. Completing kitchen work never fabricates a payment. Partial payments remain due, Khata is a signed credit ledger, and manager-authorized refunds have their own payment and receipt entries.
- Prices use Decimal; totals include the organization's configured service charge and tax settings. Cash rounding is established by the order's billing method. Staff must select the intended method when opening the order.
- Tokens, bills and refund receipts store immutable snapshots. They use the actual organization/outlet details. Browser printing opens the print dialog; no physical printer delivery is claimed. Digital tenders record payments already verified by staff at the terminal; gateway charging/verification is not implemented here. POS receipt snapshots are not an IRD certification or a completed CBMS integration.

## Live updates and performance

Commands insert an `OrderOutboxEvent` in their transaction. Celery Beat schedules `apps.orders.tasks.publish_pos_events` every second. Failed deliveries retain their error and retry with backoff. Events contain IDs and versions; clients obtain authorized REST snapshots.

`/orders/pos/socket-ticket/` issues a signed, outlet-scoped ticket valid for 60 seconds. `/ws/pos/<outlet_id>/` authenticates the ticket, checks current permissions on events/heartbeats, and renews connections after five minutes. Heartbeats record terminal presence in cache and compare the latest order/menu revision to recover missed events. The client reconnects with backoff and obtains a fresh snapshot; no browser reload is needed. Public pickup events contain only the order token/status.

Order lists are paginated, filtered on the server, and indexed by outlet/date/status. Financial summaries aggregate deduplicated orders so joins to multiple items do not multiply amounts. Only collected money appears in payment-method totals. Receipt snapshots are deferred when loading lists. Menu cards render a bounded search result and load images lazily.

CDN caching is appropriate for hashed frontend assets and content-addressed menu images. The supplied Nginx configuration gives these immutable URLs a long lifetime. Every staff POS API response is `private, no-store`; do not configure CDN caching for `/api/v1/orders/pos/` or staff customer/payment data.

## Run and deploy

1. Apply migrations with `python manage.py migrate` before rolling out the frontend.
2. Production must use PostgreSQL and `CHANNEL_LAYER_TYPE=redis`, with the existing Redis/Celery broker settings configured. The in-memory channel layer is for single-process development only.
3. Run Daphne/ASGI with the existing `/ws/` Nginx proxy, a Celery worker consuming the default queue, and one Celery Beat scheduler. The settings include the POS outbox schedule.
4. Build the frontend from the separate repository with `npm run build`; deploy its `dist` output. For local full-stack development, point `VITE_BACKEND_ORIGIN` at the local backend. Otherwise the existing Vite configuration targets the hosted backend, which needs these API changes deployed first.
5. Configure actual active tables, POS-visible menu items, recipes/stock, enabled fulfillment modes, organization billing settings, and authorized staff. This module intentionally creates no sample orders, tables, customers or payments.
6. Smoke-test two authenticated terminals in one outlet: create an order, see it in the kitchen without refresh, append a round, complete preparation, collect a split payment, allocate/collect Khata, and print a saved receipt. Verify another outlet cannot retrieve the order.

Automated coverage is in `apps/orders/test_pos.py`, including rollback, idempotency, split payments, credit collection, price changes, permissions, immutable receipts, table sessions, and WebSocket authorization. Local tests use SQLite/in-memory Channels; they do not establish production PostgreSQL/Redis capacity or physical-printer behavior.
