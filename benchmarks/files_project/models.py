"""Domain objects for a classroom shop. All money is integer kopecks."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: int
    category: str = "other"

    def __post_init__(self):
        if not self.sku or self.price < 0:
            raise ValueError("invalid product")

    def label(self):
        return f"{self.sku}: {self.name}"


@dataclass(frozen=True)
class Customer:
    identifier: str
    name: str
    email: str
    premium: bool = False

    def __post_init__(self):
        if not self.identifier or "@" not in self.email:
            raise ValueError("invalid customer")


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass
class Order:
    identifier: str
    customer_id: str
    lines: list[OrderLine] = field(default_factory=list)
    total: int = 0
    status: str = "draft"

    def add(self, sku, quantity):
        if self.status != "draft":
            raise ValueError("order is not draft")
        self.lines.append(OrderLine(sku, quantity))

    def quantities(self):
        result = {}
        for line in self.lines:
            result[line.sku] = result.get(line.sku, 0) + line.quantity
        return result

    def mark_paid(self, total):
        if self.status != "draft" or total < 0:
            raise ValueError("cannot pay")
        self.total = total
        self.status = "paid"


@dataclass(frozen=True)
class Receipt:
    order_id: str
    subtotal: int
    discount: int
    shipping: int
    total: int

    def as_dict(self):
        return {"order_id": self.order_id, "subtotal": self.subtotal,
                "discount": self.discount, "shipping": self.shipping,
                "total": self.total}


def merge_lines(lines):
    result = {}
    for line in lines:
        result[line.sku] = result.get(line.sku, 0) + line.quantity
    return [OrderLine(sku, qty) for sku, qty in sorted(result.items())]


def is_paid(order):
    return order.status == "paid"


def validate_sku(value):
    """Normalize a required sku for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("sku must be non-empty text")
    return value.strip()


def validate_customer_id(value):
    """Normalize a required customer id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("customer_id must be non-empty text")
    return value.strip()


def validate_order_id(value):
    """Normalize a required order id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("order_id must be non-empty text")
    return value.strip()


def validate_category(value):
    """Normalize a required category for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("category must be non-empty text")
    return value.strip()


def validate_email(value):
    """Normalize a required email for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("email must be non-empty text")
    return value.strip()


def validate_name(value):
    """Normalize a required name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name must be non-empty text")
    return value.strip()


def validate_status(value):
    """Normalize a required status for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("status must be non-empty text")
    return value.strip()


def validate_label(value):
    """Normalize a required label for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("label must be non-empty text")
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
