from decimal import Decimal
from textwrap import wrap
from django.utils import timezone


def format_thermal_receipt(invoice, width: int = 42) -> str:
    """
    Generates formatted ESC/POS plain-text thermal receipt for 80mm (42 cols) or 58mm (32 cols).
    Compact customer bill with item columns and cash round-down savings.
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
    if getattr(branch, 'address_line', None):
        lines.append(center(branch.address_line))
    elif restaurant.address:
        lines.append(center(restaurant.address))
    elif getattr(branch, 'city', None):
        lines.append(center(branch.city))
    if getattr(branch, 'phone_number', None):
        lines.append(center(f"Tel: {branch.phone_number}"))
    elif restaurant.phone:
        lines.append(center(f"Tel: {restaurant.phone}"))
    lines.append(center("BILL"))
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
    name_width = width - 15
    lines.append(f"{'ITEM':<{name_width}}{'QTY':>5}{'PRICE':>10}")
    lines.append(divider('-'))

    # Line Items
    for item in order.items.all():
        name = item.product_name
        if item.variant_name:
            name += f" ({item.variant_name})"
        name_lines = wrap(name, width=name_width) or ['']
        lines.append(f"{name_lines[0]:<{name_width}}{item.quantity:>5}{str(item.line_total):>10}")
        lines.extend(name_lines[1:])

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


    if invoice.cash_round_down_savings > Decimal('0.00'):
        lines.append(row("Cash Round-Down Saving:", f"-NPR {invoice.cash_round_down_savings}"))

    lines.append(divider('='))
    lines.append(row("GRAND TOTAL:", f"NPR {invoice.grand_total}"))
    lines.append(row("Payment Mode:", invoice.payment_method))
    lines.append(divider('='))

    # Footer
    lines.append(center("Thank you for visiting Crunchy Bag!"))
    lines.append(center(restaurant.website or "www.crunchybag.com"))
    lines.append(center("24-hour delivery within Kathmandu"))
    lines.append("\n\n\n")  # Feed for paper cutter

    return "\n".join(lines)

