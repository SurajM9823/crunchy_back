# Sales reports

The web admin page is `/admin?tab=sales_reports` in the sibling `crunchybag` frontend.

`GET /api/v1/orders/pos/reports/sales/?outlet_id=1&start_date=2026-10-01&end_date=2026-10-31`

Uses existing authenticated outlet billing permissions and private/no-store responses.
Dates are inclusive in Asia/Kathmandu. Omitting dates selects the last seven calendar
days including today. Maximum range: 366 days. Invalid ranges return HTTP 400.

Response fields:

- `summary`: net sales, completed order count, average order value, average daily
  sales and orders. Daily averages include every selected calendar day.
- `timeline`: zero-filled daily net sales, order counts and average order values.
- `tables`: all assigned tables ranked by net sales; unassigned orders excluded.
- `channels`: POS, TABLE_QR, KIOSK and WEBSITE totals, average order values, and
  each channel's top five products ranked by item sales.
- `top_items`: top seven products by units sold across all four channels.
- `hourly`: all 24 Nepal-time hours, totals and averages per selected calendar day.

Only COMPLETED orders qualify, attributed to their creation date. Net sales are
`max(total_payable - refunded_amount, 0)` per order, including credit sales;
this is not a cash-collection or settlement-date report. Monetary values are decimal
strings in NPR. Item sales use historical line totals before order-level discounts
and refunds. Voided lines are excluded. Refunds do not reverse item quantities,
because the order refund model does not record returned product quantities.
Products group by product ID across variants; ties use product ID for stability.

The frontend refetches through the existing outlet display WebSocket events and
heartbeat revisions, including reconnects. It cancels stale requests on filter or
outlet changes. Month selection, custom dates, last-seven-day and last-30-day presets
all apply to the entire report. Daily and hourly figures have expandable text views.

Validation: `python manage.py test apps.orders.test_reports`; frontend
`npx tsc --noEmit`, `npm run build`, and `npx playwright test tests/sales-report.spec.ts`.
