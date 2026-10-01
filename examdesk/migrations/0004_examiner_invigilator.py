from django.db import migrations, models

ROLES = {"instructor": "examiner", "ta": "invigilator"}


def rename_roles(apps, schema_editor, roles=ROLES):
    Member = apps.get_model("examdesk", "Member")
    for old, new in roles.items():
        Member.objects.filter(role=old).update(role=new)


def restore_roles(apps, schema_editor):
    rename_roles(apps, schema_editor, {new: old for old, new in ROLES.items()})


class Migration(migrations.Migration):
    dependencies = [
        ("examdesk", "0003_member_authorship"),
    ]

    operations = [
        migrations.RenameField(
            model_name="exam", old_name="instructor_token", new_name="examiner_token"
        ),
        migrations.RenameField(model_name="exam", old_name="ta_token", new_name="invigilator_token"),
        migrations.AlterField(
            model_name="member",
            name="role",
            field=models.CharField(
                choices=[("invigilator", "Invigilator"), ("examiner", "Examiner")], max_length=20
            ),
        ),
        migrations.RunPython(rename_roles, restore_roles),
    ]
