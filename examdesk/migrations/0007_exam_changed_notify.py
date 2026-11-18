from django.db import migrations

# Notifies `examdesk_exam` of each new exam version as "<id> <version>". A deleted exam counts as
# one version more. PostgreSQL sends notifications on commit, once the data is visible.
CREATE_TRIGGER = """
CREATE FUNCTION examdesk_exam_changed() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('examdesk_exam', OLD.id || ' ' || COALESCE(NEW.version, OLD.version + 1));
    RETURN NULL;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER examdesk_exam_changed AFTER UPDATE OF version OR DELETE ON examdesk_exam
FOR EACH ROW EXECUTE FUNCTION examdesk_exam_changed();
"""

DROP_TRIGGER = """
DROP TRIGGER examdesk_exam_changed ON examdesk_exam;
DROP FUNCTION examdesk_exam_changed();
"""


class Migration(migrations.Migration):
    dependencies = [("examdesk", "0006_exam_files")]

    operations = [migrations.RunSQL(CREATE_TRIGGER, DROP_TRIGGER)]
