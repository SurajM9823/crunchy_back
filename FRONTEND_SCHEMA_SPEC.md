# CRUNCHY BAG — FRONTEND SCHEMA SPECIFICATION & BUSINESS RULES
*Source of Truth: Reverse-Engineered from Frontend Codebase (`src/types/index.ts`, `AdminMenuManagerTab.tsx`, `ComboPackageModal.tsx`, `TableQrPortal.tsx`)*

---

## 1. Catalog & Product Data Models

### 1.1 Category (`catalog_category`)
- **Table**: `catalog_category`
- **Fields**:
  - `id`: `CharField(max_length=64, primary_key=True)` — Slug / identifier (e.g. `cat-burgers`). Auto-generated if not passed.
  - `name`: `CharField(max_length=120, db_index=True)` — Display name in tabs & menus.
  - `icon_name`: `CharField(max_length=64, default="Utensils")` — Lucide icon mapping for frontend.
  - `display_order`: `PositiveIntegerField(default=0)` — Tab display order.
  - `hsn_code`: `CharField(max_length=16, blank=True, default="")` — Statutory HSN/SAC code.
  - `is_archived`: `BooleanField(default=False)` — Soft deletion toggle.

### 1.2 Product (`catalog_product`)
- **Table**: `catalog_product`
- **Fields**:
  - `id`: `CharField(max_length=64, primary_key=True)` — Slug / UUID (`prod-*`).
  - `category`: `ForeignKey(Category, on_delete=models.PROTECT, related_name="products")`
  - `name`: `CharField(max_length=200, db_index=True)` — Product display name.
  - `description`: `TextField(blank=True, default="")`
  - `base_price`: `DecimalField(max_digits=10, decimal_places=2)` — Base price in NPR.
  - `cost_price`: `DecimalField(max_digits=10, decimal_places=2)` — COGS (defaults to 45% of base price if omitted).
  - `prep_time_minutes`: `PositiveIntegerField(default=12)` — Estimated kitchen prep time.
  - `calories`: `PositiveIntegerField(null=True, blank=True)` — Caloric energy count (kcal).
  - `dietary_tags`: `JSONField(default=list)` — Array of choices: `Halal`, `Spicy`, `Vegetarian`, `Chef's Choice`, `Popular`.
  - `images`: `JSONField(default=list)` — Array of image URLs (`string[]`).
  - `main_image_index`: `PositiveSmallIntegerField(default=0)` — Primary showcase image index.
  - `is_delivery_eligible`: `BooleanField(default=True)` — Filter for delivery fulfillment.
  - `is_available`: `BooleanField(default=True)` — Master stock availability toggle.
  - `is_web_visible`: `BooleanField(default=True)` — Visibility on customer website.
  - `show_on_pos`: `BooleanField(default=True)` — Visibility on cashier POS.
  - `show_on_qr`: `BooleanField(default=True)` — Visibility on Table QR digital menu.
  - `discount_percent`: `DecimalField(max_digits=5, decimal_places=2, default=0.00)` — Promotional badge discount.
  - `requires_kitchen`: `BooleanField(default=True)` — If `False`, bypasses KDS prep ticket (e.g. canned drinks, cigarettes).
  - `is_counter_direct`: `BooleanField(default=False)` — Sold directly across counter.
  - `is_direct_inventory_item`: `BooleanField(default=False)` — Sold 1:1 directly from stock inventory.
  - `linked_inventory_item`: `ForeignKey("inventory.InventoryItem", null=True, blank=True, on_delete=models.SET_NULL)`
  - `is_combo_package`: `BooleanField(default=False)` — Switches UI to Combo Configurator.
  - `combo_discount_type`: `CharField(max_length=20, null=True, blank=True)` — Choices: `percentage`, `fixed_price`, `amount_off`.
  - `combo_discount_value`: `DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)`
  - `combo_original_price`: `DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)`

### 1.3 Product Variants (`catalog_product_variant`)
- **Table**: `catalog_product_variant`
- **Fields**:
  - `id`: `CharField(max_length=64, primary_key=True)` — Slug / UUID (`var-*`).
  - `product`: `ForeignKey(Product, related_name="variants", on_delete=models.CASCADE)`
  - `name`: `CharField(max_length=100)` — Portion name (e.g. "Single Patty", "Double Patty", "Regular", "Large").
  - `price`: `DecimalField(max_digits=10, decimal_places=2)` — Absolute price overriding base price.
  - `is_default`: `BooleanField(default=False)` — Preselected default variant.

### 1.4 Modifiers & Add-ons (`catalog_modifier_group` & `catalog_modifier_option`)
- **ModifierGroup**:
  - `id`: `CharField(max_length=64, primary_key=True)` — Slug / UUID (`sec-*`).
  - `product`: `ForeignKey(Product, related_name="modifier_groups", on_delete=models.CASCADE)`
  - `name`: `CharField(max_length=120)` — e.g., "Select Artisan Bun", "Extra Sauces", "Cheese Level".
  - `min_selections`: `PositiveSmallIntegerField(default=0)`
  - `max_selections`: `PositiveSmallIntegerField(default=1)` — If `1`, frontend renders Radio buttons; if `> 1`, renders Checkboxes.
  - `required`: `BooleanField(default=False)` — If `True`, `min_selections >= 1` enforced on submit.
- **ModifierOption**:
  - `id`: `CharField(max_length=64, primary_key=True)` — Slug / UUID (`opt-*`).
  - `group`: `ForeignKey(ModifierGroup, related_name="options", on_delete=models.CASCADE)`
  - `name`: `CharField(max_length=120)` — Option label (e.g., "Butter Toasted Brioche", "Truffle Mayo").
  - `price_delta`: `DecimalField(max_digits=8, decimal_places=2, default=0.00)` — Extra surcharge (NPR).
  - `is_default`: `BooleanField(default=False)` — Pre-selected in modal.

---

## 2. Pricing, Packaging & Statutory Rules

### 2.1 Pricing Hierarchy Formula
$$\text{Unit Price} = (\text{Variant.price} \text{ if selected else } \text{Product.base\_price}) + \sum \text{ModifierOption.price\_delta}$$
$$\text{Line Total} = \text{Unit Price} \times \text{Quantity}$$

### 2.2 Time-Based Pricing Rules
1. **Product Time Slot (`catalog_product_time_pricing`)**:
   - `start_time` & `end_time` (HH:MM 24hr format)
   - `price`: Overrides base variant price during the slot
   - `days`: "All Days", "Mon-Fri", or specific days
   - `is_active`: Boolean
2. **Outlet Time Schedule (`outlet_time_pricing_schedule`)**:
   - Dynamic `discount_percentage` across multiple products during peak or happy hours per branch.

### 2.3 Statutory Tax & Currency Calculations
- **VAT Rate**: Nepal statutory 13.00% (`vatRatePercent = 13.00`).
- **Tax-Inclusive Pricing**: Menu prices already include VAT.
  $$\text{Tax Amount} = \text{round}\left(\text{Subtotal} \times \frac{0.13}{1 + 0.13}, 2\right) \quad \text{or} \quad +(\text{subtotal} \times 0.13)$$
- **Cash Round-Down Savings Rule**:
  In cash payment mode, subtotal fractions are rounded down to nearest integer:
  $$\text{Cash Savings} = \text{subtotal} - \lfloor \text{subtotal} \rfloor$$
  $$\text{Final Total} = \text{subtotal} - \text{Cash Savings}$$
- **15-Minute Quote Snapshot**: Cart quotes expire in 15 minutes (`quoteExpiresAt = Date.now() + 15 * 60 * 1000`).

---

## 3. Combos & Meal Kits Dynamic Rules

- **Static Bundles**: Configured with `Product.is_combo_package = True`. Discount type: `percentage`, `fixed_price`, or `amount_off`.
- **Dynamic Meal Kits**:
  - Removing a default item gives customer a **75% credit** of that item's base price:
    $$\text{Deduction} = \text{round}(\text{Item.base\_price} \times 0.75)$$
  - Upgrading variant/sauce: customer pays exact difference.
  - Adding extra units or extra menu items: automatically receives **10% combo discount**:
    $$\text{Extra Cost} = \text{round}((\text{variant.price} + \text{modifiers}) \times 0.90) \times \text{qty}$$
  - **Price Floor Clamp**: Minimum combo price is clamped to **NPR 150** (`max(150, calculated_price)`).

---

## 4. Outlet Scope (Global vs. Outlet-Specific)

| Scope | Entities | Purpose |
|---|---|---|
| **GLOBAL** | `catalog_category`, `catalog_product`, `catalog_modifier_*`, `OrganizationSettings`, `CustomerProfile` | Brand taxonomy, default recipes, customer loyalty across branches |
| **OUTLET-SPECIFIC** | `outlet_product_override` | Out-of-stock toggle (`is_available`), local price overrides per branch |
| **OUTLET-SPECIFIC** | `outlet_time_pricing_schedule` | Happy hour schedules targeted to specific branches |
| **OUTLET-SPECIFIC** | `order`, `kds_ticket`, `inventory_item`, `platform_device` | Branch-scoped live operations and devices |

---

## 5. Order Submission & KDS Separation

### Request Payload (`POST /api/v1/orders/`)
Supports:
- `customer_name`, `customer_phone`, `outlet_id`, `fulfillment_type` (`DELIVERY`, `TAKEAWAY`, `DRIVE_THRU`, `DINE_IN`), `order_source` (`WEBSITE`, `KIOSK`, `POS`, `TABLE_QR`), `payment_method`, `payment_status`, `notes`, `delivery_address`, `delivery_location` (`lat`, `lng`, `landmark`).
- `items`: array of line items with nested `selected_modifiers`.
- `subtotal`, `vat_included_amount`, `discount_amount`, `cash_round_down_savings`, `total_amount`.

### Key Backend Rules:
1. **Server-Side Revalidation**: Recalculate and reject client price tampering.
2. **KDS Separation**: Line items with `requires_kitchen == False` bypass KDS screen generation.
3. **Running Table Tabs**: Active `DINE_IN` sessions append new line items to `orderRounds` with incremented `roundNumber`, keeping table bill unified.
4. **Notes Prefixes**: Automatic parsing of `[Rider Tip: NPR X]`, `[Vehicle: X]`, `[Table: X]`.

