# Ongoing order rules

An order keeps one order number and bill. Each addition creates a new preparation round with its own added time, cooking start, ready time and handover time.

| Round state | Reduce/remove items | Next action |
| --- | --- | --- |
| Waiting | Yes, before billing/payment | Start round |
| Preparing | No | Mark round ready |
| Ready | No | Call and hand over this round |
| Handed over | Only if the order is still unbilled and unpaid | Continue remaining rounds |

POS and signed table QR may add rounds before billing or payment, including after completion. Confirmed website orders, dispatched deliveries and cancelled orders cannot accept additions. Completed POS/table orders may have served lines removed while still unbilled; stock is not restored for served food. A bill or any payment locks further additions and removals. Adding a round to a completed order reopens preparation and preserves earlier served rounds. A released table can be reclaimed only if no other order is active there. Refunds remain a separate financial operation; they do not undo preparation.

Example: round 1 has burgers, round 2 adds fries. Kitchen starts and finishes each round independently. TV may show `POS-12 / R1` ready and `POS-12 / R2` preparing at the same time. Staff can call and hand over round 1, then call and hand over round 2. The table is released only after every remaining round is handed over. Deliveries dispatch together after every round is ready.

## API and live updates

- Staff: `POST /api/v1/orders/pos/{id}/round/?outlet_id={outlet}` with `version`, `round_number`, and `status` (`PREPARING`, `READY`, `SERVED`). `SERVED` is for dine-in/pickup, not delivery.
- Staff call: existing `/call/` command accepts `round_number`. If multiple rounds are ready, callers must identify the round.
- Staff void: existing `/void/` accepts optional `quantity` meaning **units to remove**. Omit it to remove the whole line. Cooking/ready items are locked; served items may be removed only from completed, unbilled and unpaid POS/table orders, without restoring stock. The final entire line requires explicit order cancellation; cancellation after cooking begins is rejected.
- Table guests: `/api/v1/orders/self-service/items/void/` requires the order's self-service `tracking_token`, signed table `qr_token`, current `version`, `item_id`, and optional `quantity`. A public receipt tracking token cannot edit an order.
- Table QR additions send the current tracking token and version when available, preventing a stale tab from adding to a replacement table session.
- Responses include `rounds`, `can_append`, `partial_ready`, and per-line `can_remove`. The backend checks these rules again under the outlet write lock.
- All mutations require an `Idempotency-Key`, append audit history and use the durable order outbox. Existing WebSockets refresh POS, admin, kitchen, table QR, receipt tracking and TV without page reloads. Explicit TV calls carry their round number.

Public receipt QR links remain read-only and show each round's progress. Item edits require staff access or the signed table ordering session.

## Deployment

Run `python manage.py migrate` before deploying the corresponding backend/frontend builds. The new order migration adds nullable timestamps and reconciles existing completed/ready item states without inventing historical preparation times. Keep the existing Redis, Channels, Celery worker and outbox scheduler running. Deploy the frontend from `C:\Users\Suraj\Desktop\crunchybag`; no frontend files are stored in this backend repository for this change.


cehck my app i am so confued that in the dadmin dahsbrod when i see the order detial then i am condued it it was already billed or notif billed show bill detial in the buton if not bill then show the bill save bill also and rmov the dollar from everyehre it is the neplai rupes, if not billed then insted of print bill shhow billed, make sure that the billed item cant be billed again , there is the add item in the cimpleed order if, not bill in the app then okay to add if billed thne not let to add, tyoe item  same like as the web ui, make sure that if alread.. the detial model shoudl have the little less box bx, and then tkaing little less height the each row, alalwys keep the fixed showing prnt tokekn, bill or print bill depand on the ill status any one of thi two , make the detial model no tranparent also from the top fix the topnav writen order toek numebr with close itocn liel reduce heigt of that also make this thing, also in item show the images, in meu and all where used also if allready billed then cannot add or remvoe item, also wehn compelted then also can removethe item or add okay. the order detial must be clen make the order status update buton is in the two line make it in single row with scrollable, if custoemr number not igiven then can write in the detial but tiny better ui simple and on unupdat item it shoudl work and save or start round to learn frm website pos up and implement here also 