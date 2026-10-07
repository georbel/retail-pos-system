import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db.models.functions import TruncMonth

from .forms import (
    BranchForm, CompanyForm, DepartmentForm, LoginForm, PaymentMethodForm,
    ProductForm, StockAdjustmentForm, SupplierForm, UnitForm, UserForm,
)
from .models import (
    ActivityLog, BadOrder, BadOrderItem, Branch, CompanyProfile, Department,
    Inventory, PaymentMethod, Product, Purchase, PurchaseItem, Sale, SaleItem,
    PurchasePayment, StockMovement, Supplier, UnitOfMeasure, User, ZReading,
)

ZERO = Decimal("0.00")


def date_filter(request, key):
    raw = request.GET.get(key, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        messages.error(request, f"Choose a valid {key} date.")
        return None


def id_filter(request, key):
    raw = request.GET.get(key, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        messages.error(request, f"Choose a valid {key} filter.")
        return None


def log_activity(user, action, reference="", description=""):
    ActivityLog.objects.create(user=user, action=action, reference=reference, description=description)


def role_required(role):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")
            if request.user.role != role:
                messages.error(request, "You do not have permission to access that page.")
                return redirect("cashier" if request.user.role == User.Role.CASHIER else "backoffice")
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


admin_required = role_required(User.Role.ADMIN)
cashier_required = role_required(User.Role.CASHIER)


def active_branch_for(request):
    if request.user.role == User.Role.CASHIER:
        branch = Branch.objects.filter(pk=request.user.branch_id, active=True).first()
    else:
        raw_branch_id = request.GET.get("branch") or request.POST.get("branch")
        try:
            branch_id = int(raw_branch_id) if raw_branch_id else None
        except ValueError:
            branch_id = None
            messages.error(request, "Choose a valid branch.")
        branch = Branch.objects.filter(pk=branch_id, active=True).first() if branch_id else None
        branch = branch or Branch.objects.filter(active=True).first()
    if branch is None:
        raise Http404("An active branch must be configured for this account.")
    return branch


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(request, username=form.cleaned_data["username"], password=form.cleaned_data["password"])
        if user is None:
            messages.error(request, "Invalid username or password, or the account is inactive.")
        elif user.role == User.Role.CASHIER and (not user.branch or not user.branch.active):
            messages.error(request, "Your account needs an active branch. Contact an administrator.")
        else:
            login(request, user)
            log_activity(user, "LOGIN", description="Signed in")
            return redirect("cashier" if user.role == User.Role.CASHIER else "backoffice")
    return render(request, "pos/login.html", {"form": form})


def logout_view(request):
    if request.user.is_authenticated:
        log_activity(request.user, "LOGOUT", description="Signed out")
        logout(request)
    return redirect("login")


def dashboard(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect("cashier" if request.user.role == User.Role.CASHIER else "backoffice")


@admin_required
def backoffice(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    sales = Sale.objects.filter(voided=False)
    branch_id = request.GET.get("branch")
    if branch_id:
        sales = sales.filter(branch_id=branch_id)
    today_sales = sales.filter(created_at__date=today)
    monthly_sales = sales.filter(created_at__date__gte=month_start)
    low_stock = Inventory.objects.filter(branch__active=True, product__active=True, quantity__lte=F("product__reorder_level"))
    if branch_id:
        low_stock = low_stock.filter(branch_id=branch_id)
    daily = list(sales.filter(created_at__date__gte=today - timedelta(days=6))
                 .values("created_at__date").annotate(total=Sum("net_total")).order_by("created_at__date"))
    monthly = list(sales.filter(created_at__date__gte=today.replace(month=1, day=1))
                   .annotate(month=TruncMonth("created_at")).values("month")
                   .annotate(total=Sum("net_total")).order_by("month"))
    for chart in (daily, monthly):
        ceiling = max((row["total"] for row in chart), default=ZERO)
        for row in chart:
            row["height"] = max(8, int(row["total"] / ceiling * 120)) if ceiling else 8
    by_cashier = list(today_sales.values("cashier__full_name", "cashier__username")
                      .annotate(total=Sum("net_total")).order_by("-total")[:8])
    by_branch = list(today_sales.values("branch__name").annotate(total=Sum("net_total")).order_by("-total")[:8])
    payment_summary = list(today_sales.values("payment_method__name")
                           .annotate(total=Sum("net_total")).order_by("-total"))
    return render(request, "pos/dashboard.html", {
        "today_sales": today_sales.aggregate(total=Sum("net_total"))["total"] or ZERO,
        "monthly_sales": monthly_sales.aggregate(total=Sum("net_total"))["total"] or ZERO,
        "products_count": Product.objects.filter(active=True).count(),
        "low_stock_count": low_stock.count(),
        "suppliers_count": Supplier.objects.filter(active=True).count(),
        "transactions_count": today_sales.count(),
        "cashiers_count": User.objects.filter(role=User.Role.CASHIER, is_active=True).count(),
        "daily": daily, "monthly": monthly, "by_cashier": by_cashier,
        "by_branch": by_branch, "payment_summary": payment_summary,
        "branches": Branch.objects.filter(active=True),
    })


MASTER_FORMS = {
    "products": (Product, ProductForm, "Products", ["code", "barcode", "name", "department", "unit", "selling_price", "active"]),
    "suppliers": (Supplier, SupplierForm, "Suppliers", ["name", "contact_person", "phone", "email", "active"]),
    "branches": (Branch, BranchForm, "Branches", ["code", "name", "contact", "active"]),
    "departments": (Department, DepartmentForm, "Departments", ["name", "active"]),
    "units": (UnitOfMeasure, UnitForm, "Units of Measure", ["name", "abbreviation", "active"]),
    "payments": (PaymentMethod, PaymentMethodForm, "Payment Methods", ["name", "kind", "active"]),
    "users": (User, UserForm, "Users", ["username", "full_name", "role", "branch", "is_active"]),
    "company": (CompanyProfile, CompanyForm, "About Us", ["name", "address", "contact", "version"]),
}


@admin_required
def master_data(request, entity):
    try:
        model, form_class, title, columns = MASTER_FORMS[entity]
    except KeyError as error:
        raise Http404 from error
    query = request.GET.get("q", "").strip()
    queryset = model.objects.all().order_by("-pk")
    if entity == "company":
        queryset = queryset.order_by("pk")
    if query:
        searchable = [field.name for field in model._meta.fields if field.get_internal_type() in ("CharField", "TextField", "EmailField")]
        condition = Q()
        for field_name in searchable:
            condition |= Q(**{f"{field_name}__icontains": query})
        if condition:
            queryset = queryset.filter(condition)
    editing = None
    if request.method == "POST":
        if request.POST.get("deactivate"):
            target = get_object_or_404(model, pk=request.POST["deactivate"])
            status_field = "active" if hasattr(target, "active") else "is_active" if hasattr(target, "is_active") else None
            if status_field:
                setattr(target, status_field, False)
                target.save(update_fields=[status_field])
                log_activity(request.user, f"{title.upper()} DEACTIVATED", description=str(target))
                messages.success(request, f"{title[:-1] if title.endswith('s') else title} deactivated.")
            else:
                messages.error(request, "This record cannot be deactivated.")
            return redirect("master", entity=entity)
        instance = get_object_or_404(model, pk=request.POST.get("record_id")) if request.POST.get("record_id") else None
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            try:
                obj = form.save()
            except IntegrityError:
                messages.error(request, "That value is already in use.")
            except ValueError as error:
                messages.error(request, str(error))
            else:
                if not instance and isinstance(obj, Product):
                    for branch in Branch.objects.filter(active=True):
                        Inventory.objects.get_or_create(product=obj, branch=branch)
                elif not instance and isinstance(obj, Branch):
                    for product in Product.objects.filter(active=True):
                        Inventory.objects.get_or_create(product=product, branch=obj)
                log_activity(request.user, f"{title.upper()} {'UPDATED' if instance else 'CREATED'}", description=str(obj))
                messages.success(request, f"{title[:-1] if title.endswith('s') else title} saved.")
                return redirect("master", entity=entity)
        else:
            editing = instance
    else:
        edit_id = request.GET.get("edit")
        if edit_id:
            editing = get_object_or_404(model, pk=edit_id)
        form = form_class(instance=editing)
    if entity == "company" and not queryset.exists() and request.method == "GET":
        form = form_class()
    return render(request, "pos/master.html", {
        "entity": entity, "title": title, "columns": columns, "records": queryset[:200],
        "form": form, "editing": editing, "query": query, "is_user_form": entity == "users",
    })


@admin_required
def stock(request):
    branch = active_branch_for(request)
    form = StockAdjustmentForm(request.POST or None, initial={"branch": branch})
    if request.method == "POST" and form.is_valid():
        product = form.cleaned_data["product"]
        branch = form.cleaned_data["branch"]
        change = form.cleaned_data["quantity_change"]
        if change == 0:
            form.add_error("quantity_change", "Enter a non-zero adjustment.")
        else:
            with transaction.atomic():
                inventory, _ = Inventory.objects.select_for_update().get_or_create(product=product, branch=branch)
                if inventory.quantity + change < 0:
                    form.add_error("quantity_change", "An adjustment cannot reduce stock below zero.")
                else:
                    inventory.quantity += change
                    inventory.save(update_fields=["quantity"])
                    reference = f"ADJ-{uuid4().hex[:8].upper()}"
                    StockMovement.objects.create(
                        product=product, branch=branch, user=request.user,
                        kind=StockMovement.Kind.ADJUSTMENT, quantity_change=change,
                        balance_after=inventory.quantity, reference=reference,
                        note=form.cleaned_data["note"],
                    )
                    log_activity(request.user, "STOCK ADJUSTMENT", reference, f"{product}: {change} at {branch}")
                    messages.success(request, "Stock adjustment recorded.")
                    return redirect("stock")
    rows = Inventory.objects.filter(branch=branch).select_related("product", "product__department", "product__unit").order_by("product__name")
    query = request.GET.get("q", "")
    if query:
        rows = rows.filter(Q(product__name__icontains=query) | Q(product__code__icontains=query) | Q(product__barcode__icontains=query))
    return render(request, "pos/stock.html", {"rows": rows, "form": form, "branch": branch, "branches": Branch.objects.filter(active=True), "query": query})


def _lines_from_post(request, prefix):
    product_ids = request.POST.getlist(f"{prefix}_product[]")
    quantities = request.POST.getlist(f"{prefix}_quantity[]")
    costs = request.POST.getlist(f"{prefix}_cost[]")
    if not (len(product_ids) == len(quantities) and (not costs or len(costs) == len(product_ids))):
        raise ValueError("Each item needs a product, quantity, and valid cost.")
    lines = []
    for index, product_id in enumerate(product_ids):
        try:
            product = Product.objects.get(pk=int(product_id), active=True)
            quantity = Decimal(quantities[index])
            cost = Decimal(costs[index]) if costs else product.cost_price
        except (ValueError, InvalidOperation, Product.DoesNotExist) as error:
            raise ValueError("A selected product or quantity is invalid.") from error
        if quantity <= 0 or cost < 0:
            raise ValueError("Quantities must be positive and costs cannot be negative.")
        lines.append((product, quantity, cost))
    if not lines:
        raise ValueError("Add at least one product.")
    return lines


@admin_required
def purchases(request):
    if request.method == "POST":
        try:
            lines = _lines_from_post(request, "item")
            supplier = Supplier.objects.get(pk=request.POST.get("supplier"), active=True)
            branch = Branch.objects.get(pk=request.POST.get("branch"), active=True)
            reference = request.POST.get("reference", "").strip()
            purchase_date = date.fromisoformat(request.POST.get("purchase_date", ""))
            due_date = date.fromisoformat(request.POST["due_date"]) if request.POST.get("due_date") else None
            amount_paid = Decimal(request.POST.get("amount_paid") or "0")
            if not reference or amount_paid < 0:
                raise ValueError("Enter a reference number and a non-negative paid amount.")
            total = sum((quantity * cost for _, quantity, cost in lines), ZERO)
            if amount_paid > total:
                raise ValueError("Paid amount cannot exceed the purchase total.")
            with transaction.atomic():
                purchase = Purchase.objects.create(
                    supplier=supplier, branch=branch, created_by=request.user, reference=reference,
                    purchase_date=purchase_date, due_date=due_date, amount_paid=amount_paid,
                )
                for product, quantity, cost in lines:
                    PurchaseItem.objects.create(purchase=purchase, product=product, quantity=quantity, unit_cost=cost)
                    inventory, _ = Inventory.objects.select_for_update().get_or_create(product=product, branch=branch)
                    inventory.quantity += quantity
                    inventory.save(update_fields=["quantity"])
                    StockMovement.objects.create(product=product, branch=branch, user=request.user, kind=StockMovement.Kind.PURCHASE, quantity_change=quantity, balance_after=inventory.quantity, reference=reference, note="Supplier delivery")
                log_activity(request.user, "PURCHASE", reference, f"Received {len(lines)} product line(s), total ₱{total:,.2f}")
            messages.success(request, f"Purchase {reference} saved; stock was updated.")
            return redirect("purchases")
        except (Supplier.DoesNotExist, Branch.DoesNotExist, ValueError, InvalidOperation, IntegrityError) as error:
            messages.error(request, str(error) or "Choose an active supplier and branch.")
    records = Purchase.objects.select_related("supplier", "branch", "created_by").prefetch_related("items").order_by("-created_at")
    query = request.GET.get("q", "")
    if query:
        records = records.filter(Q(reference__icontains=query) | Q(supplier__name__icontains=query))
    supplier_id = id_filter(request, "supplier")
    if supplier_id:
        records = records.filter(supplier_id=supplier_id)
    start = date_filter(request, "start")
    end = date_filter(request, "end")
    if start and end and start > end:
        messages.error(request, "Start date cannot be after end date.")
        start = end = None
    if start:
        records = records.filter(purchase_date__gte=start)
    if end:
        records = records.filter(purchase_date__lte=end)
    return render(request, "pos/purchases.html", {
        "records": records[:100], "suppliers": Supplier.objects.filter(active=True),
        "products": Product.objects.filter(active=True).order_by("name"),
        "branches": Branch.objects.filter(active=True), "branch": active_branch_for(request),
        "query": query, "today": timezone.localdate(), "start": start.isoformat() if start else "", "end": end.isoformat() if end else "",
    })


@admin_required
def bad_orders(request):
    if request.method == "POST":
        try:
            submitted_lines = _lines_from_post(request, "item")
            quantities_by_product = defaultdict(Decimal)
            products_by_id = {}
            for product, quantity, _ in submitted_lines:
                quantities_by_product[product.pk] += quantity
                products_by_id[product.pk] = product
            lines = [(products_by_id[product_id], quantity, ZERO) for product_id, quantity in quantities_by_product.items()]
            branch = Branch.objects.get(pk=request.POST.get("branch"), active=True)
            reference = request.POST.get("reference", "").strip() or f"BO-{uuid4().hex[:8].upper()}"
            reason = request.POST.get("reason", "").strip()
            if not reason:
                raise ValueError("Enter a reason for the bad order.")
            deduct = request.POST.get("deduct_stock") == "on"
            with transaction.atomic():
                for product, quantity, _ in lines:
                    if deduct:
                        inventory = Inventory.objects.select_for_update().filter(product=product, branch=branch).first()
                        if not inventory or inventory.quantity < quantity:
                            raise ValueError(f"Insufficient stock for {product.name}.")
                record = BadOrder.objects.create(branch=branch, created_by=request.user, reference=reference, reason=reason)
                for product, quantity, _ in lines:
                    BadOrderItem.objects.create(bad_order=record, product=product, quantity=quantity, deduct_stock=deduct)
                    if deduct:
                        inventory = Inventory.objects.get(product=product, branch=branch)
                        inventory.quantity -= quantity
                        inventory.save(update_fields=["quantity"])
                        StockMovement.objects.create(product=product, branch=branch, user=request.user, kind=StockMovement.Kind.BAD_ORDER, quantity_change=-quantity, balance_after=inventory.quantity, reference=reference, note=reason)
                log_activity(request.user, "BAD ORDER", reference, reason)
            messages.success(request, "Bad order recorded.")
            return redirect("bad_orders")
        except (Branch.DoesNotExist, ValueError) as error:
            messages.error(request, str(error) or "Choose an active branch.")
    records = BadOrder.objects.select_related("branch", "created_by").prefetch_related("items__product").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(Q(reference__icontains=query) | Q(reason__icontains=query) | Q(items__product__name__icontains=query)).distinct()
    return render(request, "pos/bad_orders.html", {"records": records[:100], "products": Product.objects.filter(active=True).order_by("name"), "branches": Branch.objects.filter(active=True), "branch": active_branch_for(request), "query": query})


def cashier_summary(user, business_date=None):
    business_date = business_date or timezone.localdate()
    sales = Sale.objects.filter(cashier=user, branch=user.branch, voided=False, created_at__date=business_date)
    totals = defaultdict(lambda: ZERO)
    for row in sales.values("payment_method__kind").annotate(total=Sum("net_total")):
        totals[row["payment_method__kind"].lower()] = row["total"] or ZERO
    gross = sales.aggregate(total=Sum("gross_total"))["total"] or ZERO
    discounts = sales.aggregate(total=Sum("discount"))["total"] or ZERO
    items = SaleItem.objects.filter(sale__in=sales).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
    return {
        "sales": sales, "cash_sales": totals["cash"], "card_sales": totals["card"],
        "cheque_sales": totals["cheque"], "other_sales": sum((value for name, value in totals.items() if name not in {"cash", "card", "cheque"}), ZERO),
        "gross_sales": gross, "discounts": discounts, "net_sales": gross - discounts,
        "transactions": sales.count(), "items_sold": items, "business_date": business_date,
        "voids": Sale.objects.filter(cashier=user, branch=user.branch, voided=True, created_at__date=business_date).aggregate(total=Sum("net_total"))["total"] or ZERO,
    }


@cashier_required
def cashier(request):
    branch = active_branch_for(request)
    day = timezone.localdate()
    closed = ZReading.objects.filter(cashier=request.user, branch=branch, business_date=day).first()
    query = request.GET.get("q", "").strip()
    products = Product.objects.filter(active=True, inventories__branch=branch, inventories__quantity__gt=0).select_related("unit").order_by("name")
    if query:
        products = products.filter(Q(name__icontains=query) | Q(code__icontains=query) | Q(barcode__icontains=query))
    return render(request, "pos/cashier.html", {
        "products": products[:100], "payment_methods": PaymentMethod.objects.filter(active=True).order_by("name"),
        "summary": cashier_summary(request.user, day), "branch": branch, "closed": closed, "query": query,
    })


@cashier_required
def complete_sale(request):
    if request.method != "POST":
        return redirect("cashier")
    try:
        branch = active_branch_for(request)
        if ZReading.objects.filter(cashier=request.user, branch=branch, business_date=timezone.localdate()).exists():
            raise ValueError("This shift is already closed with a Z Reading.")
        cart = json.loads(request.POST.get("cart", "[]"))
        if not isinstance(cart, list) or not cart:
            raise ValueError("Add at least one product to the cart.")
        payment = PaymentMethod.objects.get(pk=request.POST.get("payment_method"), active=True)
        grouped = defaultdict(Decimal)
        for line in cart:
            if not isinstance(line, dict):
                raise ValueError("The cart contains an invalid item.")
            product_id = int(line.get("id"))
            quantity = Decimal(str(line.get("quantity")))
            if quantity <= 0:
                raise ValueError("Quantities must be positive.")
            grouped[product_id] += quantity
        discount = Decimal(request.POST.get("discount") or "0")
        tendered = Decimal(request.POST.get("amount_tendered") or "0")
        if discount < 0 or tendered < 0:
            raise ValueError("Discount and tendered amounts cannot be negative.")
        with transaction.atomic():
            entries = []
            gross = ZERO
            for product_id, quantity in grouped.items():
                product = Product.objects.get(pk=product_id, active=True)
                inventory = Inventory.objects.select_for_update().get(product=product, branch=branch)
                if inventory.quantity < quantity:
                    raise ValueError(f"Insufficient stock for {product.name}; available: {inventory.quantity}.")
                amount = (product.selling_price * quantity).quantize(Decimal("0.01"))
                gross += amount
                entries.append((product, inventory, quantity, amount))
            net = gross - discount
            if discount > gross:
                raise ValueError("Discount cannot exceed the subtotal.")
            if payment.kind == PaymentMethod.Kind.CASH and tendered < net:
                raise ValueError("Cash tendered is less than the total due.")
            if payment.kind != PaymentMethod.Kind.CASH and tendered == 0:
                tendered = net
            if tendered < net:
                raise ValueError("Amount tendered is less than the total due.")
            receipt_number = f"{timezone.localdate():%y%m%d}-{uuid4().hex[:8].upper()}"
            sale = Sale.objects.create(
                branch=branch, cashier=request.user, payment_method=payment,
                receipt_number=receipt_number, gross_total=gross, discount=discount, net_total=net,
                amount_tendered=tendered, change_due=max(tendered - net, ZERO) if payment.kind == PaymentMethod.Kind.CASH else ZERO,
                customer_name=request.POST.get("customer_name", "").strip(),
            )
            for product, inventory, quantity, amount in entries:
                SaleItem.objects.create(sale=sale, product=product, product_name=product.name, quantity=quantity, unit_price=product.selling_price, line_total=amount)
                inventory.quantity -= quantity
                inventory.save(update_fields=["quantity"])
                StockMovement.objects.create(product=product, branch=branch, user=request.user, kind=StockMovement.Kind.SALE, quantity_change=-quantity, balance_after=inventory.quantity, reference=receipt_number, note="Cashier sale")
            log_activity(request.user, "SALE", receipt_number, f"Net ₱{net:,.2f} paid by {payment.name}")
        messages.success(request, f"Sale completed. Receipt {receipt_number}.")
        return redirect("receipt", sale_id=sale.pk)
    except (ValueError, InvalidOperation, TypeError, json.JSONDecodeError, PaymentMethod.DoesNotExist, Product.DoesNotExist, Inventory.DoesNotExist) as error:
        messages.error(request, str(error) or "The sale could not be completed.")
        return redirect("cashier")


@cashier_required
def receipt(request, sale_id):
    sale = get_object_or_404(Sale.objects.prefetch_related("items"), pk=sale_id, cashier=request.user)
    return render(request, "pos/receipt.html", {"sale": sale, "company": CompanyProfile.objects.first()})


@cashier_required
def cashier_zreading(request):
    branch = active_branch_for(request)
    today = timezone.localdate()
    existing = ZReading.objects.filter(cashier=request.user, branch=branch, business_date=today).first()
    summary = cashier_summary(request.user, today)
    if request.method == "POST":
        if existing:
            messages.error(request, f"Z Reading {existing.number} was already completed for today.")
            return redirect("cashier_zreading")
        try:
            beginning = Decimal(request.POST.get("beginning_cash") or "0")
            actual = Decimal(request.POST.get("actual_cash") or "0")
            if beginning < 0 or actual < 0:
                raise ValueError("Cash amounts cannot be negative.")
            expected = beginning + summary["cash_sales"]
            number = f"Z-{today:%Y%m%d}-{request.user.pk}-{uuid4().hex[:4].upper()}"
            reading = ZReading.objects.create(
                cashier=request.user, branch=branch, business_date=today, number=number,
                beginning_cash=beginning, actual_cash=actual,
                cash_sales=summary["cash_sales"], card_sales=summary["card_sales"],
                cheque_sales=summary["cheque_sales"], other_sales=summary["other_sales"],
                gross_sales=summary["gross_sales"], discounts=summary["discounts"],
                returns_voids=summary["voids"], net_sales=summary["net_sales"], expected_cash=expected,
                variance=actual - expected, transactions=summary["transactions"],
                items_sold=summary["items_sold"],
            )
            log_activity(request.user, "Z READING", number, f"Net sales ₱{reading.net_sales:,.2f}; variance ₱{reading.variance:,.2f}")
            messages.success(request, f"Shift closed. Z Reading {number} has been saved.")
            return redirect("cashier_zreading")
        except (InvalidOperation, ValueError) as error:
            messages.error(request, str(error))
    return render(request, "pos/zreading.html", {"summary": summary, "reading": existing, "branch": branch})


@admin_required
def reports(request, report_type):
    if report_type not in {"inventory", "sales", "payables", "history", "zreadings"}:
        raise Http404
    context = {"report_type": report_type, "branches": Branch.objects.filter(active=True), "departments": Department.objects.all()}
    query = request.GET.get("q", "").strip()
    start = date_filter(request, "start")
    end = date_filter(request, "end")
    if start and end and start > end:
        messages.error(request, "Start date cannot be after end date.")
        start = end = None
    branch_id = id_filter(request, "branch")
    if report_type == "inventory":
        rows = Inventory.objects.select_related("product", "branch", "product__department", "product__unit").annotate(
            stock_value=ExpressionWrapper(F("quantity") * F("product__cost_price"), output_field=DecimalField(max_digits=16, decimal_places=2))
        )
        if branch_id:
            rows = rows.filter(branch_id=branch_id)
        department_id = id_filter(request, "department")
        if department_id:
            rows = rows.filter(product__department_id=department_id)
        if query:
            rows = rows.filter(Q(product__name__icontains=query) | Q(product__code__icontains=query) | Q(product__barcode__icontains=query))
        status = request.GET.get("status")
        if status == "OUT OF STOCK":
            rows = rows.filter(quantity__lte=0)
        elif status == "LOW STOCK":
            rows = rows.filter(quantity__gt=0, quantity__lte=F("product__reorder_level"))
        elif status == "IN STOCK":
            rows = rows.filter(quantity__gt=F("product__reorder_level"))
        movements = StockMovement.objects.select_related("product", "branch", "user").order_by("-created_at")
        if branch_id:
            movements = movements.filter(branch_id=branch_id)
        if start:
            movements = movements.filter(created_at__date__gte=start)
        if end:
            movements = movements.filter(created_at__date__lte=end)
        if query:
            movements = movements.filter(Q(product__name__icontains=query) | Q(product__code__icontains=query) | Q(reference__icontains=query))
        context.update({"rows": rows.order_by("product__name")[:500], "movements": movements[:200], "query": query, "start": start, "end": end, "status": status})
    elif report_type == "sales":
        rows = Sale.objects.filter(voided=False).select_related("cashier", "branch", "payment_method")
        period = request.GET.get("period")
        today = timezone.localdate()
        if period == "today":
            start = end = today
        elif period == "week":
            start = today - timedelta(days=today.weekday())
            end = today
        elif period == "month":
            start = today.replace(day=1)
            end = today
        elif period == "year":
            start = today.replace(month=1, day=1)
            end = today
        if branch_id:
            rows = rows.filter(branch_id=branch_id)
        cashier_id = id_filter(request, "cashier")
        payment_id = id_filter(request, "payment")
        if cashier_id:
            rows = rows.filter(cashier_id=cashier_id)
        if payment_id:
            rows = rows.filter(payment_method_id=payment_id)
        if start:
            rows = rows.filter(created_at__date__gte=start)
        if end:
            rows = rows.filter(created_at__date__lte=end)
        if query:
            rows = rows.filter(Q(receipt_number__icontains=query) | Q(cashier__username__icontains=query))
        context.update({"rows": rows.order_by("-created_at")[:500], "total": rows.aggregate(total=Sum("net_total"))["total"] or ZERO, "cashiers": User.objects.filter(role=User.Role.CASHIER), "payments": PaymentMethod.objects.all(), "query": query, "start": start.isoformat() if start else "", "end": end.isoformat() if end else "", "period": period})
    elif report_type == "payables":
        rows = Purchase.objects.select_related("supplier", "branch").prefetch_related("items", "payments__paid_by").order_by("-purchase_date")
        if start:
            rows = rows.filter(purchase_date__gte=start)
        if end:
            rows = rows.filter(purchase_date__lte=end)
        supplier_id = id_filter(request, "supplier")
        if supplier_id:
            rows = rows.filter(supplier_id=supplier_id)
        if query:
            rows = rows.filter(Q(supplier__name__icontains=query) | Q(reference__icontains=query))
        context.update({"rows": rows[:500], "query": query, "suppliers": Supplier.objects.all(), "start": start.isoformat() if start else "", "end": end.isoformat() if end else ""})
    elif report_type == "history":
        rows = ActivityLog.objects.select_related("user").order_by("-created_at")
        if query:
            rows = rows.filter(Q(action__icontains=query) | Q(reference__icontains=query) | Q(description__icontains=query) | Q(user__username__icontains=query))
        if start:
            rows = rows.filter(created_at__date__gte=start)
        if end:
            rows = rows.filter(created_at__date__lte=end)
        context.update({"rows": rows[:500], "query": query, "start": start, "end": end})
    else:
        rows = ZReading.objects.select_related("cashier", "branch").order_by("-created_at")
        if branch_id:
            rows = rows.filter(branch_id=branch_id)
        cashier_id = id_filter(request, "cashier")
        if cashier_id:
            rows = rows.filter(cashier_id=cashier_id)
        if start:
            rows = rows.filter(business_date__gte=start)
        if end:
            rows = rows.filter(business_date__lte=end)
        if query:
            rows = rows.filter(Q(number__icontains=query) | Q(cashier__username__icontains=query))
        context.update({"rows": rows[:500], "cashiers": User.objects.filter(role=User.Role.CASHIER), "query": query, "start": start.isoformat() if start else "", "end": end.isoformat() if end else ""})
    return render(request, "pos/report.html", context)


@admin_required
def record_purchase_payment(request, purchase_id):
    if request.method != "POST":
        return redirect("report", report_type="payables")
    try:
        amount = Decimal(request.POST.get("amount", ""))
        with transaction.atomic():
            purchase = get_object_or_404(Purchase.objects.select_for_update(), pk=purchase_id)
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero.")
            balance = purchase.balance
            if amount > balance:
                raise ValueError(f"Payment exceeds the remaining balance of ₱{balance:,.2f}.")
            purchase.amount_paid += amount
            purchase.save(update_fields=["amount_paid"])
            PurchasePayment.objects.create(
                purchase=purchase, amount=amount, reference=request.POST.get("reference", "").strip(),
                paid_by=request.user,
            )
            log_activity(request.user, "SUPPLIER PAYMENT", purchase.reference, f"Paid ₱{amount:,.2f} to {purchase.supplier.name}")
        messages.success(request, f"Payment of ₱{amount:,.2f} recorded for {purchase.reference}.")
    except (ValueError, InvalidOperation) as error:
        messages.error(request, str(error))
    return redirect("report", report_type="payables")


@admin_required
def void_sale(request, sale_id):
    if request.method != "POST":
        return redirect("report", report_type="sales")
    with transaction.atomic():
        sale = get_object_or_404(Sale.objects.select_for_update(), pk=sale_id, voided=False)
        if ZReading.objects.filter(cashier=sale.cashier, branch=sale.branch, business_date=timezone.localtime(sale.created_at).date()).exists():
            messages.error(request, "A sale included in a completed Z Reading cannot be voided.")
            return redirect("report", report_type="sales")
        sale.voided = True
        sale.save(update_fields=["voided"])
        for item in sale.items.select_related("product"):
            inventory, _ = Inventory.objects.select_for_update().get_or_create(product=item.product, branch=sale.branch)
            inventory.quantity += item.quantity
            inventory.save(update_fields=["quantity"])
            StockMovement.objects.create(product=item.product, branch=sale.branch, user=request.user, kind=StockMovement.Kind.ADJUSTMENT, quantity_change=item.quantity, balance_after=inventory.quantity, reference=sale.receipt_number, note="Sale voided")
        log_activity(request.user, "SALE VOIDED", sale.receipt_number, "Sale voided and stock restored")
    messages.success(request, f"Sale {sale.receipt_number} voided; inventory restored.")
    return redirect("report", report_type="sales")
