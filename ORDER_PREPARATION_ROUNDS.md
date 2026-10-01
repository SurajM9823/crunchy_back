# Ongoing order rules

An order keeps one order number and bill. Each addition creates a new preparation round with its own added time, cooking start, ready time and handover time.

| Round state | Reduce/remove items | Next action |
| --- | --- | --- |
| Waiting | Yes, before billing/payment | Start round |
| Preparing | No | Mark round ready |
| Ready | No | Call and hand over this round |
| Handed over | No | Continue remaining rounds |

POS and signed table QR may add rounds while the order is open. Confirmed website orders, dispatched deliveries and completed/cancelled orders cannot accept additions. Ordering after final handover requires a new order. Refunds remain a separate financial operation; they do not undo preparation.

Example: round 1 has burgers, round 2 adds fries. Kitchen starts and finishes each round independently. TV may show `POS-12 / R1` ready and `POS-12 / R2` preparing at the same time. Staff can call and hand over round 1, then call and hand over round 2. The table is released only after every remaining round is handed over. Deliveries dispatch together after every round is ready.

## API and live updates

- Staff: `POST /api/v1/orders/pos/{id}/round/?outlet_id={outlet}` with `version`, `round_number`, and `status` (`PREPARING`, `READY`, `SERVED`). `SERVED` is for dine-in/pickup, not delivery.
- Staff call: existing `/call/` command accepts `round_number`. If multiple rounds are ready, callers must identify the round.
- Staff void: existing `/void/` accepts optional `quantity` meaning **units to remove**. Omit it to remove the whole line. Cooking/ready/served items are locked. The final entire line requires explicit order cancellation; cancellation after cooking begins is rejected.
- Table guests: `/api/v1/orders/self-service/items/void/` requires the order's self-service `tracking_token`, signed table `qr_token`, current `version`, `item_id`, and optional `quantity`. A public receipt tracking token cannot edit an order.
- Table QR additions send the current tracking token and version when available, preventing a stale tab from adding to a replacement table session.
- Responses include `rounds`, `can_append`, `partial_ready`, and per-line `can_remove`. The backend checks these rules again under the outlet write lock.
- All mutations require an `Idempotency-Key`, append audit history and use the durable order outbox. Existing WebSockets refresh POS, admin, kitchen, table QR, receipt tracking and TV without page reloads. Explicit TV calls carry their round number.

Public receipt QR links remain read-only and show each round's progress. Item edits require staff access or the signed table ordering session.

## Deployment

Run `python manage.py migrate` before deploying the corresponding backend/frontend builds. The new order migration adds nullable timestamps and reconciles existing completed/ready item states without inventing historical preparation times. Keep the existing Redis, Channels, Celery worker and outbox scheduler running. Deploy the frontend from `C:\Users\Suraj\Desktop\crunchybag`; no frontend files are stored in this backend repository for this change.
