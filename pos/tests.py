import json
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from django.urls import reverse

from config.settings import get_database_config

from .models import (
    ActivityLog, Branch, Department, Inventory, PaymentMethod, Product, Purchase,
    PurchaseItem, PurchasePayment, Sale, SaleItem, StockMovement, Supplier,
    UnitOfMeasure, User, ZReading,
)


class DatabaseConfigurationTests(SimpleTestCase):
    def test_local_development_defaults_to_sqlite(self):
        config = get_database_config({}, base_dir=Path("project"))

        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], Path("project") / "db.sqlite3")

    def test_vercel_uses_writable_sqlite_and_copies_the_packaged_database(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            base_dir = Path(directory) / "app"
            base_dir.mkdir()
            packaged_database = base_dir / "db.sqlite3"
            packaged_database.write_bytes(b"packaged sqlite database")
            writable_database = Path(directory) / "tmp" / "pos.sqlite3"

            config = get_database_config(
                {"VERCEL": "1", "SQLITE_PATH": str(writable_database)},
                base_dir=base_dir,
            )

            self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
            self.assertEqual(config["NAME"], writable_database)
            self.assertEqual(writable_database.read_bytes(), packaged_database.read_bytes())
            self.assertEqual(config["OPTIONS"], {"timeout": 20})


class StaticAssetDeploymentTests(SimpleTestCase):
    def test_design_styles_are_referenced_and_collected_for_deployment(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "/static/pos/app.css")
        self.assertContains(response, "/static/pos/confirm.css")
        self.assertContains(response, "/static/pos/app.js")

        with tempfile.TemporaryDirectory() as directory:
            static_root = Path(directory)
            with override_settings(STATIC_ROOT=static_root):
                call_command("collectstatic", interactive=False, verbosity=0)

            for asset in ("pos/app.css", "pos/confirm.css", "pos/app.js"):
                with self.subTest(asset=asset):
                    self.assertTrue((static_root / asset).is_file())


class POSWorkflowTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Test Branch", code="TEST")
        self.admin = User.objects.create_user(username="manager", password="StrongPass123!", role=User.Role.ADMIN)
        self.cashier = User.objects.create_user(username="clerk", password="StrongPass123!", role=User.Role.CASHIER, branch=self.branch)
        self.supplier = Supplier.objects.create(name="Example Supplier")
        self.department = Department.objects.create(name="Grocery")
        self.unit = UnitOfMeasure.objects.create(name="Piece", abbreviation="pc")
        self.product = Product.objects.create(code="ITEM-1", barcode="1000001", name="Sample item", department=self.department, unit=self.unit, supplier=self.supplier, cost_price=Decimal("4.00"), selling_price=Decimal("10.00"), reorder_level=2)
        self.inventory = Inventory.objects.create(product=self.product, branch=self.branch, quantity=Decimal("20"))
        self.cash = PaymentMethod.objects.get(name="Cash")
        self.card = PaymentMethod.objects.get(name="Card")

    def test_single_login_redirects_by_role_and_protects_backoffice(self):
        response = self.client.post(reverse("login"), {"username": "manager", "password": "StrongPass123!"})
        self.assertRedirects(response, reverse("backoffice"))
        self.client.get(reverse("logout"))
        response = self.client.post(reverse("login"), {"username": "clerk", "password": "StrongPass123!"})
        self.assertRedirects(response, reverse("cashier"))
        response = self.client.get(reverse("backoffice"))
        self.assertRedirects(response, reverse("cashier"))

    def test_purchase_receipt_adds_stock_and_records_movement(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("purchases"), {
            "supplier": self.supplier.pk, "branch": self.branch.pk,
            "reference": "INV-001", "purchase_date": "2026-10-06", "amount_paid": "0",
            "item_product[]": [self.product.pk], "item_quantity[]": ["5"], "item_cost[]": ["4.00"],
        })
        self.assertRedirects(response, reverse("purchases"))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, Decimal("25"))
        self.assertTrue(StockMovement.objects.filter(kind=StockMovement.Kind.PURCHASE, reference="INV-001").exists())

    def test_bad_order_deducts_stock_and_records_reason(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("bad_orders"), {
            "branch": self.branch.pk, "reference": "BO-001", "reason": "Damaged",
            "deduct_stock": "on", "item_product[]": [self.product.pk],
            "item_quantity[]": ["3"],
        })
        self.assertRedirects(response, reverse("bad_orders"))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, Decimal("17"))
        movement = StockMovement.objects.get(kind=StockMovement.Kind.BAD_ORDER)
        self.assertEqual(movement.quantity_change, Decimal("-3"))
        self.assertEqual(movement.note, "Damaged")

    def test_void_restores_inventory_and_excludes_sale_from_active_sales(self):
        self.client.force_login(self.cashier)
        self.client.post(reverse("complete_sale"), {
            "cart": json.dumps([{"id": self.product.pk, "quantity": 2}]),
            "payment_method": self.cash.pk, "amount_tendered": "20", "discount": "0",
        })
        sale = Sale.objects.get()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("void_sale", args=[sale.pk]))
        self.assertRedirects(response, reverse("report", args=["sales"]))
        sale.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertTrue(sale.voided)
        self.assertEqual(self.inventory.quantity, Decimal("20"))
        self.assertEqual(Sale.objects.filter(voided=False).count(), 0)

    def test_cash_sale_deducts_inventory_and_persists_receipt(self):
        self.client.force_login(self.cashier)
        response = self.client.post(reverse("complete_sale"), {
            "cart": json.dumps([{"id": self.product.pk, "quantity": 2}]),
            "payment_method": self.cash.pk, "amount_tendered": "25", "discount": "0",
        })
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.get()
        self.assertEqual(sale.net_total, Decimal("20.00"))
        self.assertEqual(sale.change_due, Decimal("5.00"))
        self.assertEqual(SaleItem.objects.filter(sale=sale).count(), 1)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, Decimal("18"))
        self.assertTrue(StockMovement.objects.filter(kind=StockMovement.Kind.SALE, reference=sale.receipt_number).exists())
        self.assertTrue(ActivityLog.objects.filter(action="SALE", reference=sale.receipt_number).exists())

    def test_cash_sale_rejects_insufficient_tender_and_stock(self):
        self.client.force_login(self.cashier)
        response = self.client.post(reverse("complete_sale"), {
            "cart": json.dumps([{"id": self.product.pk, "quantity": 2}]),
            "payment_method": self.cash.pk, "amount_tendered": "19", "discount": "0",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Sale.objects.exists())
        response = self.client.post(reverse("complete_sale"), {
            "cart": json.dumps([{"id": self.product.pk, "quantity": 21}]),
            "payment_method": self.card.pk, "amount_tendered": "210", "discount": "0",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Sale.objects.exists())
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, Decimal("20"))

    def test_supplier_payments_update_balance_and_keep_payment_history(self):
        purchase = Purchase.objects.create(
            supplier=self.supplier, branch=self.branch, created_by=self.admin,
            reference="INV-PAY", purchase_date="2026-10-06",
        )
        PurchaseItem.objects.create(purchase=purchase, product=self.product, quantity=5, unit_cost=4)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("purchase_payment", args=[purchase.pk]), {"amount": "10.00", "reference": "BANK-1"})
        self.assertRedirects(response, reverse("report", args=["payables"]))
        purchase.refresh_from_db()
        self.assertEqual(purchase.amount_paid, Decimal("10.00"))
        self.assertEqual(purchase.balance, Decimal("10.00"))
        self.assertEqual(PurchasePayment.objects.get(purchase=purchase).reference, "BANK-1")
        self.client.post(reverse("purchase_payment", args=[purchase.pk]), {"amount": "11.00"})
        purchase.refresh_from_db()
        self.assertEqual(purchase.amount_paid, Decimal("10.00"))
        self.assertEqual(PurchasePayment.objects.filter(purchase=purchase).count(), 1)

    def test_product_creation_creates_branch_inventory_and_allows_blank_barcode(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("master", args=["products"]), {
            "code": "ITEM-2", "barcode": "", "name": "Another item",
            "department": self.department.pk, "unit": self.unit.pk,
            "supplier": self.supplier.pk, "cost_price": "2.00", "selling_price": "5.00",
            "reorder_level": "3", "active": "on",
        })
        self.assertRedirects(response, reverse("master", args=["products"]))
        product = Product.objects.get(code="ITEM-2")
        self.assertIsNone(product.barcode)
        self.assertEqual(Inventory.objects.get(product=product, branch=self.branch).quantity, Decimal("0"))

    def test_z_reading_is_saved_once_and_closes_sales_for_day(self):
        self.client.force_login(self.cashier)
        self.client.post(reverse("complete_sale"), {
            "cart": json.dumps([{"id": self.product.pk, "quantity": 1}]),
            "payment_method": self.cash.pk, "amount_tendered": "10", "discount": "0",
        })
        response = self.client.post(reverse("cashier_zreading"), {"beginning_cash": "100", "actual_cash": "110"})
        self.assertRedirects(response, reverse("cashier_zreading"))
        reading = ZReading.objects.get()
        self.assertEqual(reading.cash_sales, Decimal("10.00"))
        self.assertEqual(reading.expected_cash, Decimal("110.00"))
        self.assertEqual(reading.variance, Decimal("0.00"))
        self.client.post(reverse("cashier_zreading"), {"beginning_cash": "100", "actual_cash": "110"})
        self.assertEqual(ZReading.objects.count(), 1)
        self.client.post(reverse("complete_sale"), {
            "cart": json.dumps([{"id": self.product.pk, "quantity": 1}]),
            "payment_method": self.cash.pk, "amount_tendered": "10", "discount": "0",
        })
        self.assertEqual(Sale.objects.count(), 1)

    def test_admin_and_cashier_pages_render_from_their_shared_session(self):
        self.client.force_login(self.admin)
        for url in (
            reverse("backoffice"), reverse("master", args=["products"]),
            reverse("master", args=["suppliers"]), reverse("master", args=["branches"]),
            reverse("master", args=["departments"]), reverse("master", args=["units"]),
            reverse("master", args=["payments"]), reverse("master", args=["users"]),
            reverse("master", args=["company"]), reverse("stock"),
            reverse("purchases"), reverse("bad_orders"),
            *(reverse("report", args=[name]) for name in ("inventory", "sales", "payables", "history", "zreadings")),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.client.get(reverse("logout"))
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("cashier")).status_code, 200)
        self.assertEqual(self.client.get(reverse("cashier_zreading")).status_code, 200)
