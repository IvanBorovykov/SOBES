"""Read-only summaries over saved orders and stock."""
from collections import Counter
from pricing import format_money


def paid_orders(repository):
    return repository.list_orders(status="paid")


def revenue(repository):
    return sum(order.total for order in paid_orders(repository))


def average_order(repository):
    orders = paid_orders(repository)
    if not orders:
        return 0
    return sum(order.total for order in orders) // len(orders)


def top_products(repository, limit=5):
    quantities = Counter()
    for order in paid_orders(repository):
        quantities.update(order.quantities())
    return quantities.most_common(limit)


def customer_spending(repository):
    result = Counter()
    for order in paid_orders(repository):
        result[order.customer_id] += order.total
    return dict(result)


def stock_report(repository):
    return [{"sku": sku, "quantity": repository.stock[sku],
             "value": repository.stock[sku] * product.price}
            for sku, product in sorted(repository.products.items())]


def dashboard(repository):
    return {"revenue": revenue(repository),
            "average_order": average_order(repository),
            "top_products": top_products(repository),
            "low_stock": repository.low_stock()}


def render_dashboard(repository):
    data = dashboard(repository)
    return "\n".join([f"Revenue: {format_money(data['revenue'])}",
                       f"Average: {format_money(data['average_order'])}",
                       f"Low stock: {', '.join(data['low_stock'])}"])


def order_status_counts(repository):
    return dict(Counter(order.status for order in repository.list_orders()))


def total_units_sold(repository):
    return sum(sum(order.quantities().values()) for order in paid_orders(repository))


def customer_summary(repository, customer_id):
    orders = [o for o in paid_orders(repository) if o.customer_id == customer_id]
    return {"customer": customer_id, "orders": len(orders),
            "spent": sum(o.total for o in orders)}


def find_orders_with_product(repository, sku):
    return [order.identifier for order in paid_orders(repository)
            if sku in order.quantities()]


def has_sales(repository):
    return bool(paid_orders(repository))


def best_customer(repository):
    spending = customer_spending(repository)
    return max(spending, key=spending.get) if spending else None


def validate_report_name(value):
    """Normalize a required report name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("report_name must be non-empty text")
    return value.strip()


def validate_column_name(value):
    """Normalize a required column name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("column_name must be non-empty text")
    return value.strip()


def validate_filter_name(value):
    """Normalize a required filter name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("filter_name must be non-empty text")
    return value.strip()


def validate_metric_name(value):
    """Normalize a required metric name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("metric_name must be non-empty text")
    return value.strip()


def validate_group_name(value):
    """Normalize a required group name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("group_name must be non-empty text")
    return value.strip()


def validate_period_name(value):
    """Normalize a required period name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("period_name must be non-empty text")
    return value.strip()


def validate_sort_name(value):
    """Normalize a required sort name for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("sort_name must be non-empty text")
    return value.strip()


def validate_export_title(value):
    """Normalize a required export title for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("export_title must be non-empty text")
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
