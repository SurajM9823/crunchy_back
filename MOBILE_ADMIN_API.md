# Mobile admin: login, dashboard and order management

Use your backend origin followed by the paths below.

## 1. Password login and session

`POST /api/v1/auth/outlet-login/`

```json
{"identifier":"staff-username-or-email-or-phone","password":"staff-password"}
```

Returns `access`, `refresh`, `user` (including role, branch_id and assigned_pages), and `outlet`. Use existing admin/staff account credentials; this change creates no accounts. Waiters can log in; billing still requires billing permission. Disabled employee profiles are rejected.

Send `Authorization: Bearer <access>` for all staff requests. Store tokens in the mobile platform's secure storage. Refresh with `POST /api/v1/auth/token/refresh/` and `{"refresh":"<refresh>"}`; save both tokens returned by rotation. `GET /api/v1/auth/me/` returns the profile.

Append `?outlet_id=<numeric-branch-id>` to every POS request. Assigned staff can omit it; owners/superusers without an assigned branch must select an authorized outlet. Cross-outlet access is checked by the backend.

`GET /api/v1/orders/pos/meta/` returns tables, enabled payment methods and permissions (`orders`, `billing`, `kitchen`, `discount`, `refund`, `delete_order`). Use these permissions to display actions. Staff assignments and roles remain enforced on the server.

## 2. Dashboard and detail

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/orders/pos/dashboard/` | Today's orders and financial summary, Nepal calendar date |
| GET | `/api/v1/orders/pos/dashboard/?all_dates=true` | All dates, paginated |
| GET | `/api/v1/orders/pos/dashboard/?start_date=2026-10-01&end_date=2026-10-08` | Inclusive date range |
| GET | `/api/v1/orders/pos/<id>/` | Open one order |
| GET | `/api/v1/catalog/menu/?channel=pos&outlet_id=<id>` | Available menu for adding items |

When another query parameter exists, append outlet selection with `&outlet_id=...`.

The dashboard returns `results`, `count`, `page`, `page_size`, `summary`, `status_counts`, `filters`, and `timezone`. Default page size is 25; supported sizes are 10, 25, 50, 100. Filters: `status`, `fulfillment`, `settlement`, `search`, `page`, `page_size`. A single date selects that day; `all_dates=true` cannot be combined with dates. Status counts are computed before the status filter; financial summary follows all filters and covers all pages. Summary excludes cancelled sales. `due` includes credit balances.

Order fields include `id`, `order_number` (the display/token number, e.g. `POS-01`), `version`, customer/table details, source, status, items, preparation rounds, totals, payments and receipt IDs. Use numeric `id` in URLs. Amounts are decimal strings. Detail flags `can_append` and item `can_remove` describe allowed editing states; role permissions also apply.

These endpoints cover orders in the current staff-managed workflow, including current website, kiosk and table QR orders. Legacy orders with `is_pos_managed=false` are outside this POS workflow. The existing `/api/v1/orders/pos/` list remains available without a default date restriction.

## 3. Manage an order

All paths below start with `/api/v1/orders/pos/`. Send JSON and the authorization/outlet context above. Every mutation needs a unique `Idempotency-Key` header and the latest order `version`. Quotes are read-only POSTs and do not need that header. Mutation responses return the updated order: replace local state, including version.

### Add items

First `POST quote/`:

```json
{"order_id":123,"items":[{"product_id":7,"quantity":2}]}
```

Then `POST 123/append/`, using the quote's `total_payable` as `expected_total`, its `order_version` as `version`, and the same items:

```json
{"version":3,"items":[{"product_id":7,"quantity":2}],"expected_total":"600.00"}
```

The amount above is illustrative; always copy the actual server quotation. Variants/modifiers/combo selections follow the catalog quote schema. Appending adds a preparation round.

### Remove items

`POST 123/void/`:

```json
{"version":4,"item_id":456,"quantity":1,"reason":"Customer removed item"}
```

Use the order line's `id`, not the catalog product ID. Omit quantity to remove the entire line. Waiting items may be reduced or removed before preparation; served items on completed POS/table orders may be removed only while the order is still unbilled and unpaid. Served-item removal does not restore stock. Confirmed website orders, billed/paid orders, and dispatched deliveries are locked; respect the returned flags and validation errors.

### Prepare a bill

`POST 123/billing-quote/` with `{"version":5}` previews totals. Optional `customer_phone` and `discount_amount` participate in pricing. Manual discount changes require discount permission.

`POST 123/bill/` locks the bill and saves its receipt. A bill can only be locked once; use the saved receipt for bill details or reprinting:

```json
{"version":5,"customer_name":"Customer Name","customer_phone":"9800000011"}
```

Set customer details before billing when credit is needed. Billing alone does not collect payment. Customer/discount changes are restricted after billing or payment.

### Collect cash, split payment or credit

`POST 123/settle/` with the current version. Cash example:

```json
{"version":6,"tenders":[{"method":"CASH","amount":"600.00"}]}
```

Split cash + digital payment:

```json
{"version":6,"tenders":[{"method":"CASH","amount":"200.00"},{"method":"FONEPAY","amount":"400.00","reference":"verified-reference"}]}
```

Credit/Khata (requires customer name and valid phone):

```json
{"version":6,"tenders":[{"method":"CREDIT","amount":"600.00"}]}
```

Mix CASH and CREDIT in the same tenders list for partial cash plus credit. Later collect credit using `settle/` with real payment tenders and the latest version; the credit balance is reduced. Use actual due amounts from the API. Payment methods must be enabled for the outlet. Digital tenders record payments verified by staff; this API does not charge a wallet. Fulfillment and payment status are independent.

### Order status and receipts

`POST 123/transition/` with `{"version":7,"status":"ACCEPTED"}` accepts a pending order. Normal progression is PENDING → ACCEPTED → PREPARING → READY → COMPLETED; delivery can pass through OUT_FOR_DELIVERY. Preparation rules may require all rounds to be ready before handover.

`GET receipts/<receipt_id>/` returns the saved receipt document. Receipt IDs are in order detail's `receipts` array.

## Mobile retries and errors

- For a timeout or lost response, retry the exact same body with the same idempotency key. Persist uncertain mutations until resolved.
- For a new action, use a new key. Never reuse a key with a changed request body.
- HTTP 409: fetch current order/quote, let the user review changes, then submit a new action with the new version and key.
- HTTP 400: show validation errors. HTTP 401: refresh the session or sign in. HTTP 403: insufficient permission or unavailable outlet. HTTP 404: unavailable order/receipt.
- On returning to the foreground, reload dashboard/detail. Existing socket-ticket APIs provide live foreground updates; FCM provides background new-order alerts.

## 4. Background new-order alerts

After staff login, the Android app registers its Firebase Cloud Messaging (FCM) token:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/orders/pos/notifications/device/` | Register or refresh this Android device's token |
| DELETE | `/api/v1/orders/pos/notifications/device/` | Deactivate this device token (also performed when staff signs out) |

Both endpoints require the staff bearer token and the same authorized `outlet_id` context as other POS requests. The request body is `{"token":"<FCM-device-token>","platform":"android"}`. Registration is outlet-scoped and only devices belonging to users who currently have order-read access receive notifications. Only new `ORDER_CREATE` outbox events are pushed; this covers order-creation paths that record the shared order outbox (POS, website, QR and kiosk). WebSocket updates remain enabled for the foreground app.

Deployment requirements:

1. Create/configure the Firebase Android app for application ID `com.aistudio.crunchybag.kypwzt` and put its `google-services.json` in the Android app module (`app/google-services.json`) before building. Do not send the file or service-account credentials in chat.
2. Configure the backend's `FIREBASE_PROJECT_ID` and Firebase Admin Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` pointing to a protected service-account file, or the hosting platform's workload identity). Grant that identity permission to send Firebase Cloud Messaging messages.
3. Apply the database migration and run both the Celery worker and Celery Beat. Beat schedules push dispatch every five seconds; Redis/Celery and the existing outbox infrastructure must be available.
4. Allow notification permission on Android 13 and later, and ensure notifications are enabled for the app/channel on the device.

Delivery is retried after transient Firebase errors. Invalid tokens are deactivated. Android force-stop is a platform limitation: notifications resume after the user manually launches the app again. Until Firebase app configuration and server credentials are installed, the app can still use its existing foreground WebSocket updates, but background push delivery is unavailable.
