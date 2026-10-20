"""In-memory repository for one process; data disappears on restart."""
from copy import deepcopy


class Repository:
    def __init__(self):
        self.products = {}
        self.customers = {}
        self.orders = {}
        self.stock = {}
        self.audit = []

    def add_product(self, product, quantity=0):
        if product.sku in self.products or quantity < 0:
            raise ValueError("invalid or duplicate product")
        self.products[product.sku] = product
        self.stock[product.sku] = quantity

    def add_customer(self, customer):
        if customer.identifier in self.customers:
            raise ValueError("duplicate customer")
        self.customers[customer.identifier] = customer

    def save_order(self, order):
        self.orders[order.identifier] = deepcopy(order)
        self.audit.append(("save", order.identifier))

    def get_order(self, identifier):
        return deepcopy(self.orders[identifier])

    def reserve(self, quantities):
        # Validate the whole request before changing any stock.
        for sku, quantity in quantities.items():
            if quantity <= 0 or self.stock.get(sku, 0) < quantity:
                raise ValueError("insufficient stock")
        for sku, quantity in quantities.items():
            self.stock[sku] -= quantity
        self.audit.append(("reserve", dict(quantities)))

    def release(self, quantities):
        for sku, quantity in quantities.items():
            if sku not in self.stock or quantity <= 0:
                raise ValueError("invalid release")
        for sku, quantity in quantities.items():
            self.stock[sku] += quantity
        self.audit.append(("release", dict(quantities)))

    def list_orders(self, status=None):
        return [deepcopy(order) for order in self.orders.values()
                if status is None or order.status == status]

    def low_stock(self, threshold=3):
        return sorted(sku for sku, quantity in self.stock.items()
                      if quantity < threshold)

    def snapshot(self):
        return deepcopy({"products": self.products, "customers": self.customers,
                         "orders": self.orders, "stock": self.stock})

    def restore(self, snapshot):
        self.products = deepcopy(snapshot["products"])
        self.customers = deepcopy(snapshot["customers"])
        self.orders = deepcopy(snapshot["orders"])
        self.stock = deepcopy(snapshot["stock"])
        self.audit.append(("restore", None))

    def counts(self):
        return {"products": len(self.products), "customers": len(self.customers),
                "orders": len(self.orders)}


def exported_stock(repository):
    return dict(sorted(repository.stock.items()))


def inventory_value(repository):
    return sum(repository.products[sku].price * quantity
               for sku, quantity in repository.stock.items())


def validate_product_key(value):
    """Normalize a required product key for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("product_key must be non-empty text")
    return value.strip()


def validate_customer_key(value):
    """Normalize a required customer key for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("customer_key must be non-empty text")
    return value.strip()


def validate_order_key(value):
    """Normalize a required order key for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("order_key must be non-empty text")
    return value.strip()


def validate_snapshot_name(value):
    """Normalize a required snapshot name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("snapshot_name must be non-empty text")
    return value.strip()


def validate_audit_label(value):
    """Normalize a required audit label for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("audit_label must be non-empty text")
    return value.strip()


def validate_stock_key(value):
    """Normalize a required stock key for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("stock_key must be non-empty text")
    return value.strip()


def validate_export_name(value):
    """Normalize a required export name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("export_name must be non-empty text")
    return value.strip()


def validate_source_name(value):
    """Normalize a required source name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_name must be non-empty text")
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
