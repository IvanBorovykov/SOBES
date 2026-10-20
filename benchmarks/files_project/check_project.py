"""Executable answer key for the synthetic course project; no network."""
from pathlib import Path
import ast
from models import Product,Customer
from storage import Repository
from service import CheckoutService
from reporting import revenue


def setup(price=10000):
    repository=Repository()
    repository.add_product(Product('BOOK','Textbook',price),3)
    repository.add_customer(Customer('C','Student','student@example.invalid',True))
    service=CheckoutService(repository)
    order=service.create_order('C')
    service.add_item(order.identifier,'BOOK',1)
    return repository,service,order

r,s,o=setup()
receipt=s.checkout(o.identifier,'STUDENT')
assert receipt.subtotal==10000 and receipt.discount==1500 and receipt.shipping==0 and receipt.total==8500
assert revenue(r)==8500 and r.stock['BOOK']==2
s.cancel(o.identifier)
assert revenue(r)==0 and r.stock['BOOK']==3
s.cancel(o.identifier)
assert r.stock['BOOK']==3  # Cancellation is idempotent.

r,s,o=setup(9999)
receipt=s.quote(o.identifier,'STUDENT')
assert receipt.shipping==750 and receipt.total==9250

r,s,o=setup()
save=r.save_order
def failing_save(order):
    if order.status=='paid':raise RuntimeError('simulated persistence failure')
    save(order)
r.save_order=failing_save
try:s.checkout(o.identifier,'STUDENT')
except RuntimeError:pass
else:raise AssertionError('Failure must propagate')
assert r.stock['BOOK']==3 and r.get_order(o.identifier).status=='draft'

tree=ast.parse(Path(__file__).with_name('service.py').read_text())
callers=sorted(node.name for node in ast.walk(tree) if isinstance(node,ast.FunctionDef)
               and any(isinstance(call,ast.Call) and isinstance(call.func,ast.Name)
                       and call.func.id=='BBB' for call in ast.walk(node)))
assert callers==['checkout','quote']
for filename in ['models.py','pricing.py','storage.py','service.py','reporting.py']:
    names={node.name for node in ast.walk(ast.parse(Path(__file__).with_name(filename).read_text()))
           if isinstance(node,ast.FunctionDef)}
    assert 'send_email' not in names
print('PROJECT_GOLD_OK: totals, shipping boundary, cancel, double cancel, rollback, callers, absent email function')
