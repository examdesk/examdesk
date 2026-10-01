import django.db.models.deletion
from django.db import migrations, models

AUTHORED = {  # model: path to its exam, authors' role if they are no longer members
    "Answer": ("clarification__exam_id", "instructor"),
    "Announcement": ("exam_id", "instructor"),
    "Report": ("clarification__exam_id", "ta"),
    "Delivery": ("report__clarification__exam_id", "ta"),
    "Readout": ("announcement__exam_id", "ta"),
}


def link_members(apps, schema_editor):
    """Point each row at the member with its `by` name, or at a removed member made for it."""
    Member = apps.get_model("examdesk", "Member")
    found = {}
    for model_name, (exam_path, role) in AUTHORED.items():
        model = apps.get_model("examdesk", model_name)
        for pk, exam_id, name in model.objects.values_list("pk", exam_path, "by"):
            key = (exam_id, name.casefold())
            if key not in found:
                found[key] = Member.objects.filter(
                    exam_id=exam_id, name__iexact=name
                ).first() or Member.objects.create(
                    exam_id=exam_id, name=name, role=role, removed=True
                )
            model.objects.filter(pk=pk).update(member=found[key])


def member_field(null):
    return models.ForeignKey(
        null=null,
        on_delete=django.db.models.deletion.RESTRICT,
        related_name="+",
        to="examdesk.member",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("examdesk", "0002_seatclaim_member"),
    ]

    operations = [
        migrations.AddField(
            model_name="member", name="removed", field=models.BooleanField(default=False)
        ),
        *(
            migrations.AddField(model_name=name.lower(), name="member", field=member_field(True))
            for name in AUTHORED
        ),
        migrations.RunPython(link_members, migrations.RunPython.noop),
        *(migrations.RemoveField(model_name=name.lower(), name="by") for name in AUTHORED),
        *(
            migrations.AlterField(model_name=name.lower(), name="member", field=member_field(False))
            for name in AUTHORED
        ),
    ]
