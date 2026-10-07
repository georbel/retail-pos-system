from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("backoffice/", views.backoffice, name="backoffice"),
    path("cashier/", views.cashier, name="cashier"),
    path("cashier/sale/", views.complete_sale, name="complete_sale"),
    path("cashier/receipt/<int:sale_id>/", views.receipt, name="receipt"),
    path("cashier/z-reading/", views.cashier_zreading, name="cashier_zreading"),
    path("backoffice/master/<slug:entity>/", views.master_data, name="master"),
    path("backoffice/stock/", views.stock, name="stock"),
    path("backoffice/purchases/", views.purchases, name="purchases"),
    path("backoffice/bad-orders/", views.bad_orders, name="bad_orders"),
    path("backoffice/reports/<slug:report_type>/", views.reports, name="report"),
    path("backoffice/purchases/<int:purchase_id>/payment/", views.record_purchase_payment, name="purchase_payment"),
    path("backoffice/sales/<int:sale_id>/void/", views.void_sale, name="void_sale"),
]
