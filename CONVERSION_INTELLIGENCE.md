# Website conversion intelligence

> Historical implementation notes below describe the custom analytics version.
> The active implementation now uses PostHog for visitor reporting and Django for
> business totals. See DEPLOYMENT.md for current configuration and the background
> report worker. The legacy Traffic history and custom diagnosis workspace are
> not the current `/admin?tab=analytics` interface.

The admin workspace is `/admin?tab=analytics` in `../crunchybag`. Existing page-view analytics remain available under **Traffic history**. Collection starts with this release; historical page views cannot reconstruct earlier carts or checkouts.

## Audit and implementation

The original site used `useWebsiteTraffic`, `/customer/traffic/`, anonymous salted visitor/session hashes, outlet-scoped audience REST APIs and WebSocket revision notifications. Checkout creates a real website order with a manually uploaded QR payment receipt; staff verify payment later. No Meta reporting API, payment gateway callback, delivery service-area validator, delivery fee calculator or screen-replay provider was present.

The extension reuses visitor/session identity, privacy opt-outs, outlet authorization and audience notifications. New page-view batches also populate the old `WebsiteVisit` table using the same event UUID, preventing duplicate legacy visits. QR/kiosk tracking keeps the original endpoint.

Implemented phases:

1. Allowlisted event batches and indexed journey/session/ad/report models.
2. Anonymous browser collection for navigation, visible products, cart changes, checkout completion steps, QR interactions, errors, attribution and Web Vitals.
3. Server-authored order-created, order-confirmed and payment-verified events. Browser requests cannot submit these outcome names.
4. Outlet-scoped session search, chronological timelines, live/abandoned lists, debugger, CSV/JSON exports and asynchronous reports.
5. Conversion dashboard, source/campaign/ad comparisons, product/cart/checkout/payment/error/performance analysis, reconciliation and evidence-based diagnosis.
6. Previous-seven-day comparison with minimum sample thresholds and optional AI evidence selection.

## Run and deploy

From the backend:

```powershell
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py check
```

Run the existing Redis, Django ASGI server, Celery worker and Celery Beat services. Beat schedules `apps.customer_web.tasks.build_journey_reports` every three seconds. Reports use durable database jobs, short row-lock claims, bounded retries and stale-worker recovery. Without worker/Beat, reports remain queued; ingestion and journey APIs still accept/read events.

Typical separate service commands (use the project's existing process manager in production):

```text
celery -A crunchy_backend worker --loglevel=info
celery -A crunchy_backend beat --loglevel=info
```

Windows local workers may need `--pool=solo`. For a one-shot local check without a broker:

```powershell
.venv\Scripts\python.exe manage.py shell -c "from apps.customer_web.tasks import build_journey_reports; build_journey_reports()"
```

From the frontend:

```powershell
npm.cmd install
npm.cmd run lint
npm.cmd run build
```

The new browser dependency is `web-vitals`. Keep the existing API/ASGI URLs and outlet configuration. This implementation does not publish either application or change production credentials.

## Measurement semantics

- Date/time selection uses Asia/Kathmandu and sessions **starting** within the range. Associated journey events may occur later. Reconciliation separately counts website database orders **created** in the range, irrespective of segment filters; the scopes are explicitly labelled.
- A live session was seen within 120 seconds. Abandonment requires cart/checkout activity, no trusted order-created event and more than 30 minutes of inactivity. Intent is a declared heuristic, not a prediction about a person's motives.
- Funnel rows report distinct sessions/users and event counts. Adjacent transition rates require the next step to occur after the prior step. Optional steps and post-order manual payment review mean the complete funnel is not a mandatory linear checkout. The core funnel is Website → Products → Cart → Checkout → Order.
- Browser errors are observed friction. Leaving after an error does not prove causation. Unknown data stays unknown. Baseline alerts require at least 30 sessions in both periods and five prior conversions; they are observational signals.
- Tracked order count means server-created orders, not paid orders. Overview order value excludes cancelled orders; product line revenue is before order-level discounts. Verified payments are shown separately.
- Reports cap at 100,000 events and display truncation. Narrow the range before using capped totals. Session/event pagination and exports are independent of the report cap.
- Payment proof upload or opening a QR image is not proof of a bank transaction. `payment_started` is an observed manual-QR interaction. Provider declines/cancellations cannot be automatically observed without a gateway integration.
- Daily advertising aggregates cannot be divided by unsupported browser/time/product segments; those aggregate KPIs show unavailable for incompatible filters.

## Advertising data

UTM fields and `fbclid`, campaign/ad-set/ad IDs and names are retained as sanitized attribution. Landing arrivals do not substitute for Meta click counts. Use **Ads → Import daily ad metrics** as an owner/manager. JSON import accepts at most 500 rows and replaces the same outlet/date/source/ad row, so reimporting does not double spend.

```json
[
  {
    "date": "2026-10-09",
    "source": "facebook",
    "campaign_id": "123456789012345",
    "adset_id": "223456789012345",
    "ad_id": "323456789012345",
    "impressions": 1200,
    "clicks": 300,
    "spend": "1500.00"
  }
]
```

Use spend in NPR to compare it with order value. Automatic Meta sync, Pixel/CAPI delivery and external click reconciliation are not configured. No external tracking scripts were added.

## Optional AI

The dashboard works without AI credentials, returning local rules-based findings with metric references. To enable AI-assisted selection, set both server-only variables:

```dotenv
ANALYTICS_OPENAI_API_KEY=
ANALYTICS_OPENAI_MODEL=
```

Choose a model supporting Responses API Structured Outputs. The worker sends sanitized aggregate evidence and the question, with `store: false`. The model can select only existing metric IDs and diagnostic finding indices. Displayed numbers and conclusions come from the stored report, not generated metric text. Session IDs, customer contacts, precise coordinates, form values, payment proofs and credentials are excluded. External AI calls were not exercised without credentials.

## Privacy, reliability and access

- Do Not Track / Global Privacy Control and staff exclusion apply to browser collection. A visitor ID lasts 90 days; a session renews after 30 minutes of inactivity. The server stores salted hashes, not raw identity UUIDs.
- Browser events are batched, bounded and queued in session storage. Retries reuse event IDs. Pagehide uses beacon delivery with a retained retry copy; the server deduplicates event IDs.
- Metadata keys are allowlisted. URL queries, form values, phone/email, precise location and request bodies are not collected. Search stores length/result count, not the query. Error collection stores categories/endpoints/statuses, not exception bodies.
- Public collection is throttled and timestamps are bounded. It cannot be treated as tamper-proof business data; purchase/payment outcomes come from server records.
- Analytics reads, exports and imports reuse existing outlet authorization. Private responses use `private, no-store`. Public WebSocket notifications expose revision identifiers only, with data re-fetched through authenticated REST.
- Existing daily pruning deletes visits and sessions older than 90 days and report snapshots older than two days. Ad aggregates remain until explicitly managed. Exports escape spreadsheet formula prefixes.

## Verification

```powershell
.venv\Scripts\python.exe manage.py test apps.customer_web.test_journeys apps.customer_web.tests apps.orders.test_pos --keepdb
```

```powershell
npm.cmd run test:pos -- --grep "conversion intelligence|website analytics"
npm.cmd run test:customer -- --grep "anonymous journey|website traffic|checkout carries|rejected checkout"
```

The backend acceptance scenarios cover a successful verified order, payment failure followed by abandonment, product-only browsing, and a delivery-area failure. The latter two failure event types are simulated fixtures; the current site has no external payment failure callback or serviceability validator. Additional tests cover privacy allowlisting, duplicate delivery, delayed browser batches, forged purchase rejection, outlet access, ad imports, asynchronous caching and exports. Browser tests cover the dashboard, preserved traffic history, filters, journey details, analyst evidence, export downloads, anonymous attribution, privacy opt-outs and checkout attribution.

After deployment, open the customer site, add a product, submit a test order and inspect **Event Debugger → User Journeys → Tracking Health**. Verify the server order ID against the database, then verify payment through the existing staff workflow. Ad, fee, serviceability and screen-replay coverage remain explicitly unavailable until their real integrations are added.

References: [Web Vitals](https://github.com/GoogleChrome/web-vitals), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
