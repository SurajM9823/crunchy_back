from decimal import Decimal
from django.utils import timezone


def format_thermal_receipt(invoice, width: int = 42) -> str:
    """
    Generates formatted ESC/POS plain-text thermal receipt for 80mm (42 cols) or 58mm (32 cols).
    Includes Statutory PAN, 13% Tax-inclusive VAT breakdown, and cash round-down savings.
    """
    branch = invoice.branch
    restaurant = invoice.restaurant
    order = invoice.order

    lines = []

    def center(text: str) -> str:
        return text.center(width)

    def row(left: str, right: str) -> str:
        space = width - len(left) - len(right)
        if space < 1:
            return f"{left} {right}"
        return f"{left}{' ' * space}{right}"

    def divider(char: str = '-') -> str:
        return char * width

    # Header
    lines.append(center(restaurant.name.upper()))
    lines.append(center(branch.name))
    if getattr(branch, 'address_line', None):
        lines.append(center(branch.address_line))
    elif getattr(branch, 'city', None):
        lines.append(center(branch.city))
    if getattr(branch, 'phone_number', None):
        lines.append(center(f"Tel: {branch.phone_number}"))
    lines.append(center(f"PAN NO: {invoice.seller_pan}"))
    lines.append(center("TAX INVOICE"))
    lines.append(divider('='))

    # Meta
    lines.append(row("Invoice No:", invoice.invoice_number))
    lines.append(row("Date & Time:", invoice.created_at.strftime('%Y-%m-%d %H:%M')))
    lines.append(row("Order No:", order.order_number))
    lines.append(row("Fulfillment:", order.get_fulfillment_type_display()))
    if order.table:
        lines.append(row("Table:", order.table.table_number))
    if invoice.customer_name and invoice.customer_name != "Guest":
        lines.append(row("Customer:", invoice.customer_name))
    if invoice.customer_pan:
        lines.append(row("Buyer PAN:", invoice.customer_pan))

    lines.append(divider('-'))
    lines.append(f"{'ITEM':<20}{'QTY':>5}{'PRICE':>8}{'TOTAL':>9}")
    lines.append(divider('-'))

    # Line Items
    for item in order.items.all():
        name = item.product_name
        if item.variant_name:
            name += f" ({item.variant_name})"
        if len(name) > 20:
            name = name[:18] + ".."

        lines.append(f"{name:<20}{item.quantity:>5}{str(item.unit_price):>8}{str(item.line_total):>9}")

        # Show modifiers if any
        for mod in item.modifiers.all():
            delta_str = f"+{mod.price_delta}" if mod.price_delta > 0 else "Free"
            mod_line = f"  + {mod.option_name}"
            lines.append(f"{mod_line:<33}{delta_str:>9}")

    lines.append(divider('-'))

    # Financial Totals
    lines.append(row("Gross Subtotal:", f"NPR {invoice.subtotal}"))
    if order.discount_amount > Decimal('0.00'):
        lines.append(row("Discount:", f"-NPR {order.discount_amount}"))

    lines.append(row("Taxable Amount (Net):", f"NPR {invoice.taxable_amount}"))
    lines.append(row("Included VAT (13%):", f"NPR {invoice.vat_amount}"))

    if invoice.cash_round_down_savings > Decimal('0.00'):
        lines.append(row("Cash Round-Down Saving:", f"-NPR {invoice.cash_round_down_savings}"))

    lines.append(divider('='))
    lines.append(row("GRAND TOTAL:", f"NPR {invoice.grand_total}"))
    lines.append(row("Payment Mode:", invoice.payment_method))
    lines.append(divider('='))

    # Footer
    lines.append(center("Prices are inclusive of all statutory taxes."))
    lines.append(center("Thank you for visiting Crunchy Bag!"))
    lines.append(center("www.crunchybag.com"))
    lines.append("\n\n\n")  # Feed for paper cutter

    return "\n".join(lines)

