"""Generate a small runnable five-module shop for grounded voice QA tests."""
from pathlib import Path
import ast
import hashlib
import json

ROOT=Path(__file__).resolve().parent
SOURCES={
'models.py': '''"""Domain objects for a classroom shop. All money is integer kopecks."""
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
''',
'pricing.py': '''"""Price rules. No floating point arithmetic; amounts are kopecks."""
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
''',
'storage.py': '''"""In-memory repository for one process; data disappears on restart."""
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
''',
'service.py': '''"""Application workflow; calls pricing rules and the repository."""
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
''',
'reporting.py': '''"""Read-only summaries over saved orders and stock."""
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
    return "\\n".join([f"Revenue: {format_money(data['revenue'])}",
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
'''
}
manifest={}
for filename,source in SOURCES.items():
    # Useful field validation helpers give each module roughly 150 lines of code.
    fields={'models.py':['sku','customer_id','order_id','category','email','name','status','label'],
            'pricing.py':['coupon','currency','price_label','discount_label','receipt_id','tax_code','region','shipping_zone'],
            'storage.py':['product_key','customer_key','order_key','snapshot_name','audit_label','stock_key','export_name','source_name'],
            'service.py':['request_id','session_id','actor_id','trace_id','operation','correlation_id','channel','reason'],
            'reporting.py':['report_name','column_name','filter_name','metric_name','group_name','period_name','sort_name','export_title']}[filename]
    fields += ['external_reference', 'display_title']
    for field in fields:
        source+=f'''\n\ndef validate_{field}(value):
    """Normalize a required {field.replace('_',' ')} for this module's boundary."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{field} must be non-empty text")
    return value.strip()
'''
    path=ROOT/filename;path.write_text(source)
    tree=ast.parse(source)
    manifest[filename]={'lines':len(source.splitlines()),'sha256':hashlib.sha256(source.encode()).hexdigest(),
      'symbols':{node.name:{'line':node.lineno,'end':node.end_lineno} for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.ClassDef))}}
(ROOT/'project_manifest.json').write_text(json.dumps(manifest,indent=2))
print({name:info['lines'] for name,info in manifest.items()})
