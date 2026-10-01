import django.db.models.deletion
from django.db import migrations, models


def delete_claims(apps, schema_editor):
    # Claims last minutes at most; existing ones have no member to point to.
    apps.get_model("examdesk", "SeatClaim").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("examdesk", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(delete_claims, migrations.RunPython.noop),
        migrations.RemoveField(model_name="seatclaim", name="by"),
        migrations.AddField(
            model_name="seatclaim",
            name="member",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="examdesk.member",
            ),
        ),
    ]
