"""Application workflow; calls pricing rules and the repository."""
from models import Order
from pricing import BBB


class CheckoutService:
    def __init__(self, repository):
        self.repository = repository
        self.next_id = 1

    def create_order(self, customer_id):
        if customer_id not in self.repository.customers:
            raise KeyError("customer not found")
        identifier = f"ORD-{self.next_id:04d}"
        self.next_id += 1
        order = Order(identifier, customer_id)
        self.repository.save_order(order)
        return order

    def add_item(self, order_id, sku, quantity):
        if sku not in self.repository.products:
            raise KeyError("product not found")
        order = self.repository.get_order(order_id)
        order.add(sku, quantity)
        self.repository.save_order(order)
        return order

    def checkout(self, order_id, coupon=None):
        order = self.repository.get_order(order_id)
        if order.status != "draft" or not order.lines:
            raise ValueError("cannot checkout")
        customer = self.repository.customers[order.customer_id]
        receipt = BBB(order, self.repository.products, customer, coupon)
        quantities = order.quantities()
        self.repository.reserve(quantities)
        try:
            order.mark_paid(receipt.total)
            self.repository.save_order(order)
        except Exception:
            self.repository.release(quantities)
            raise
        return receipt

    def cancel(self, order_id):
        order = self.repository.get_order(order_id)
        if order.status == "cancelled":
            return order
        if order.status == "paid":
            self.repository.release(order.quantities())
        order.status = "cancelled"
        self.repository.save_order(order)
        return order

    def quote(self, order_id, coupon=None):
        order = self.repository.get_order(order_id)
        customer = self.repository.customers[order.customer_id]
        return BBB(order, self.repository.products, customer, coupon)

    def customer_orders(self, customer_id):
        return [order for order in self.repository.list_orders()
                if order.customer_id == customer_id]

    def remove_item(self, order_id, sku):
        order = self.repository.get_order(order_id)
        if order.status != "draft":
            raise ValueError("order is not draft")
        order.lines = [line for line in order.lines if line.sku != sku]
        self.repository.save_order(order)
        return order


def order_total(service, identifier):
    return service.repository.get_order(identifier).total


def has_customer(service, identifier):
    return identifier in service.repository.customers


def validate_request_id(value):
    """Normalize a required request id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("request_id must be non-empty text")
    return value.strip()


def validate_session_id(value):
    """Normalize a required session id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("session_id must be non-empty text")
    return value.strip()


def validate_actor_id(value):
    """Normalize a required actor id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("actor_id must be non-empty text")
    return value.strip()


def validate_trace_id(value):
    """Normalize a required trace id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("trace_id must be non-empty text")
    return value.strip()


def validate_operation(value):
    """Normalize a required operation for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("operation must be non-empty text")
    return value.strip()


def validate_correlation_id(value):
    """Normalize a required correlation id for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("correlation_id must be non-empty text")
    return value.strip()


def validate_channel(value):
    """Normalize a required channel for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("channel must be non-empty text")
    return value.strip()


def validate_reason(value):
    """Normalize a required reason for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reason must be non-empty text")
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
