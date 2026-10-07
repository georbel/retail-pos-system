from django.db import migrations


def classify_methods(apps, schema_editor):
    PaymentMethod = apps.get_model("pos", "PaymentMethod")
    for name, kind in (("Cash", "CASH"), ("Card", "CARD"), ("Cheque", "CHEQUE")):
        PaymentMethod.objects.filter(name=name).update(kind=kind)


class Migration(migrations.Migration):
    dependencies = [("pos", "0003_purchasepayment_paymentmethod_kind_and_more")]

    operations = [migrations.RunPython(classify_methods, migrations.RunPython.noop)]
