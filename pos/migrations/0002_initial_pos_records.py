from django.db import migrations


def create_defaults(apps, schema_editor):
    Branch = apps.get_model("pos", "Branch")
    PaymentMethod = apps.get_model("pos", "PaymentMethod")
    Branch.objects.get_or_create(code="MAIN", defaults={"name": "Main Branch", "active": True})
    for name in ("Cash", "Card", "Cheque"):
        PaymentMethod.objects.get_or_create(name=name, defaults={"active": True})


def remove_defaults(apps, schema_editor):
    Branch = apps.get_model("pos", "Branch")
    PaymentMethod = apps.get_model("pos", "PaymentMethod")
    Branch.objects.filter(code="MAIN", name="Main Branch").delete()
    PaymentMethod.objects.filter(name__in=("Cash", "Card", "Cheque")).delete()


class Migration(migrations.Migration):
    dependencies = [("pos", "0001_initial")]

    operations = [migrations.RunPython(create_defaults, remove_defaults)]
