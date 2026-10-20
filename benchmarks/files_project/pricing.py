"""Price rules. No floating point arithmetic; amounts are kopecks."""
from models import Receipt

FREE_SHIPPING_THRESHOLD = 10000
SHIPPING_FEE = 750
COUPONS = {"STUDENT": 10, "WELCOME": 5}


def subtotal_for(order, products):
    total = 0
    for line in order.lines:
        product = products[line.sku]
        total += product.price * line.quantity
    return total


def coupon_percent(code):
    if code is None:
        return 0
    normalized = code.strip().upper()
    if normalized not in COUPONS:
        raise ValueError("unknown coupon")
    return COUPONS[normalized]


def discount_percent(customer, coupon=None):
    premium = 7 if customer.premium else 0
    return min(15, premium + coupon_percent(coupon))


def shipping_for(subtotal):
    if subtotal >= FREE_SHIPPING_THRESHOLD:
        return 0
    return SHIPPING_FEE


def BBB(order, products, customer, coupon=None):
    """Build a receipt; shipping threshold uses subtotal BEFORE discount."""
    subtotal = subtotal_for(order, products)
    percent = discount_percent(customer, coupon)
    discount = subtotal * percent // 100
    shipping = shipping_for(subtotal)
    total = subtotal - discount + shipping
    return Receipt(order.identifier, subtotal, discount, shipping, total)


def explain_discount(customer, coupon=None):
    percent = discount_percent(customer, coupon)
    return f"Discount: {percent}%"


def price_after_discount(amount, percent):
    if amount < 0 or not 0 <= percent <= 100:
        raise ValueError("invalid discount arguments")
    return amount - amount * percent // 100


def format_money(amount):
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    return f"{sign}{amount // 100}.{amount % 100:02d}"


def quote_quantity(product, quantity):
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return product.price * quantity


def compare_prices(first, second):
    return first.price - second.price


def eligible_coupons(customer):
    return tuple(sorted(COUPONS))


def receipt_summary(receipt):
    return f"{receipt.order_id}: {format_money(receipt.total)}"


def validate_coupon(value):
    """Normalize a required coupon for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("coupon must be non-empty text")
    return value.strip()


def validate_currency(value):
    """Normalize a required currency for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("currency must be non-empty text")
    return value.strip()


def validate_price_label(value):
    """Normalize a required price label for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("price_label must be non-empty text")
    return value.strip()


def validate_discount_label(value):
    """Normalize a required discount label for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("discount_label must be non-empty text")
    return value.strip()


def validate_receipt_id(value):
    """Normalize a required receipt id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("receipt_id must be non-empty text")
    return value.strip()


def validate_tax_code(value):
    """Normalize a required tax code for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("tax_code must be non-empty text")
    return value.strip()


def validate_region(value):
    """Normalize a required region for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("region must be non-empty text")
    return value.strip()


def validate_shipping_zone(value):
    """Normalize a required shipping zone for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("shipping_zone must be non-empty text")
    return value.strip()


def validate_external_reference(value):
    """Normalize a required external reference for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("external_reference must be non-empty text")
    return value.strip()


def validate_display_title(value):
    """Normalize a required display title for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("display_title must be non-empty text")
    return value.strip()
