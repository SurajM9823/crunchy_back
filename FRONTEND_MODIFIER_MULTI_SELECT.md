# Modifier Multi-Select: Frontend Handoff

## Goal
Let menu admins configure whether a modifier group is single- or multi-select, then let customers and staff choose options within its limits. Backend support already exists; no new `allow_multiple` API field is needed.

## Admin Menu Editor
Read modifier groups from each product's `modifier_groups` array. Each group has:

- `id`, `name`
- `required`
- `min_selections`
- `max_selections`
- `options`: each has `id`, `name`, `price_delta`, `is_default`

Save groups with the product using `POST /api/v1/catalog/products/?outlet_id={outletId}` or `PATCH /api/v1/catalog/products/{productId}/?outlet_id={outletId}`. Send the full `modifier_groups` list when editing a product.

Suggested controls:

- **Allow multiple selections** off: set `max_selections` to `1`; show a single-choice control.
- On: set `max_selections` to the allowed maximum (at least `2`); show a numeric maximum input and checkboxes.
- Let admins set `min_selections`; `required` means at least one selection. If `required` is on and minimum is blank/zero, backend normalizes the minimum to `1`.
- Require `0 <= min_selections <= max_selections <= options.length`; backend rejects invalid limits and more default options than the maximum.
- Preserve existing group and option `id` values on edits. Omitted nested options are removed by backend synchronization.

Example group in a product write:

```json
{
  "id": "sec-toppings-a1",
  "name": "Toppings",
  "required": true,
  "min_selections": 1,
  "max_selections": 2,
  "options": [
    {"id": "opt-cheese-a1", "name": "Cheese", "price_delta": "30.00", "is_default": false},
    {"id": "opt-chili-a1", "name": "Chili", "price_delta": "10.00", "is_default": false},
    {"id": "opt-onion-a1", "name": "Onion", "price_delta": "0.00", "is_default": false}
  ]
}
```

## Ordering UI
For each product line, keep selected modifier option IDs in an array named `modifier_option_ids`. A group with `max_selections == 1` is single-choice; a group with `max_selections > 1` is multi-choice. Enforce each group's minimum and maximum in the UI, and show each option's `price_delta`. Send the combined option IDs from all groups for that product line.

```json
{
  "product_id": "prod-burger-a1",
  "quantity": 1,
  "variant_id": "var-large-a1",
  "modifier_option_ids": ["opt-cheese-a1", "opt-chili-a1"]
}
```

For combo products, put `modifier_option_ids` on the relevant item inside `combo_selections`, not on the outer combo line.

## Order Channels
Use the same item shape in every channel:

- Customer web: quote with `POST /api/v1/customer/checkout/quote/`; submit checkout to `POST /api/v1/customer/checkout/` with the items in the JSON `payload` plus the required receipt file.
- Table QR and kiosk: quote with `POST /api/v1/orders/self-service/quote/`; submit to `POST /api/v1/orders/self-service/checkout/`. Set `order_source` to `TABLE_QR` or `KIOSK`. QR also sends its signed `qr_token`.
- POS: quote with `POST /api/v1/orders/pos/quote/?outlet_id={outletId}`; create with `POST /api/v1/orders/pos/?outlet_id={outletId}`.

After any selection change, request a fresh quote and use its total as `expected_total`. The backend quote/order validation is authoritative: it rejects duplicate, unrelated, below-minimum, or above-maximum selections and calculates each selected option's surcharge. Keep the selected option array when retrying or submitting the order.
