from decimal import Decimal

from django.contrib.auth.models import AbstractUser, UserManager
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Branch(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    contact = models.CharField(max_length=60, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class POSUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        return super().create_superuser(username, email=email, password=password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        CASHIER = "CASHIER", "Cashier"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CASHIER)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="users")
    full_name = models.CharField(max_length=160, blank=True)
    objects = POSUserManager()

    @property
    def display_name(self):
        return self.full_name or self.get_full_name() or self.username


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class UnitOfMeasure(models.Model):
    name = models.CharField(max_length=60, unique=True)
    abbreviation = models.CharField(max_length=12, unique=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.abbreviation


class Supplier(models.Model):
    name = models.CharField(max_length=140)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class PaymentMethod(models.Model):
    class Kind(models.TextChoices):
        CASH = "CASH", "Cash"
        CARD = "CARD", "Card"
        CHEQUE = "CHEQUE", "Cheque"
        OTHER = "OTHER", "Other"

    name = models.CharField(max_length=50, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    code = models.CharField(max_length=40, unique=True)
    barcode = models.CharField(max_length=80, unique=True, null=True, blank=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
    unit = models.ForeignKey(UnitOfMeasure, null=True, blank=True, on_delete=models.SET_NULL)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    reorder_level = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Inventory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="inventories")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="inventories")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "branch"], name="unique_product_branch_inventory")]

    @property
    def stock_status(self):
        if self.quantity <= 0:
            return "OUT OF STOCK"
        return "LOW STOCK" if self.quantity <= self.product.reorder_level else "IN STOCK"


class StockMovement(models.Model):
    class Kind(models.TextChoices):
        OPENING = "OPENING", "Opening balance"
        PURCHASE = "PURCHASE", "Purchase"
        SALE = "SALE", "Sale"
        BAD_ORDER = "BAD_ORDER", "Bad order"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity_change = models.DecimalField(max_digits=12, decimal_places=3)
    balance_after = models.DecimalField(max_digits=12, decimal_places=3)
    reference = models.CharField(max_length=50, blank=True)
    note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    reference = models.CharField(max_length=60)
    purchase_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["supplier", "branch", "reference"], name="unique_supplier_branch_invoice")]

    @property
    def total(self):
        return sum((item.quantity * item.unit_cost for item in self.items.all()), Decimal("0.00"))

    @property
    def balance(self):
        return max(self.total - self.amount_paid, Decimal("0.00"))

    @property
    def payable_status(self):
        if self.balance == 0:
            return "PAID"
        if self.due_date and self.due_date < timezone.localdate():
            return "OVERDUE"
        return "PARTIALLY PAID" if self.amount_paid else "UNPAID"


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])


class PurchasePayment(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reference = models.CharField(max_length=60, blank=True)
    paid_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    paid_at = models.DateTimeField(default=timezone.now)


class BadOrder(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    reference = models.CharField(max_length=60)
    reason = models.CharField(max_length=180)
    created_at = models.DateTimeField(default=timezone.now)


class BadOrderItem(models.Model):
    bad_order = models.ForeignKey(BadOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    deduct_stock = models.BooleanField(default=True)


class Sale(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sales")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT)
    receipt_number = models.CharField(max_length=40, unique=True)
    gross_total = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_tendered = models.DecimalField(max_digits=12, decimal_places=2)
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customer_name = models.CharField(max_length=120, blank=True)
    voided = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def business_date(self):
        return timezone.localtime(self.created_at).date()


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=160)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)


class ZReading(models.Model):
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name="z_readings")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    business_date = models.DateField()
    number = models.CharField(max_length=40, unique=True)
    beginning_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    actual_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    card_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cheque_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gross_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discounts = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    returns_voids = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    variance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transactions = models.PositiveIntegerField(default=0)
    items_sold = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cashier", "branch", "business_date"], name="unique_cashier_branch_z_date")]


class ActivityLog(models.Model):
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    reference = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class CompanyProfile(models.Model):
    name = models.CharField(max_length=160, default="POS System")
    address = models.TextField(blank=True)
    contact = models.CharField(max_length=100, blank=True)
    version = models.CharField(max_length=30, default="1.0.0")

    def __str__(self):
        return self.name
