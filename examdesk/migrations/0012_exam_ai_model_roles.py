from django.db import migrations, models
from django.db.models import F


def copy_careful_model_to_fast_model(apps, schema_editor):
    Exam = apps.get_model("examdesk", "Exam")
    Exam.objects.update(ai_fast_model=F("ai_careful_model"))


class Migration(migrations.Migration):
    dependencies = [("examdesk", "0011_exam_venues")]

    operations = [
        migrations.RenameField(
            model_name="exam", old_name="ai_text_model", new_name="ai_careful_model"
        ),
        migrations.AlterField(
            model_name="exam",
            name="ai_careful_model",
            field=models.CharField(blank=True, max_length=200, verbose_name="careful model"),
        ),
        migrations.AddField(
            model_name="exam",
            name="ai_fast_model",
            field=models.CharField(blank=True, max_length=200, verbose_name="fast model"),
        ),
        migrations.RunPython(copy_careful_model_to_fast_model, migrations.RunPython.noop),
    ]
