from django.db import migrations, models


def fail_unsent_emails(apps, schema_editor):
    # Email sending is removed: anything still queued would never go out
    apps.get_model('ui', 'QueuedEmail').objects.filter(status__in=['pending', 'sending']).update(
        status='failed', last_error='Not sent: email was replaced by Telegram',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('ui', '0011_queuedemail_category'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='telegram_chat_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='telegram_chat_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='telegram_link_token',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(fail_unsent_emails, migrations.RunPython.noop),
        # Keep the queue's history: rename the table instead of replacing it
        migrations.RenameModel('QueuedEmail', 'QueuedMessage'),
        migrations.RenameIndex('queuedmessage', new_name='ui_queuedme_status_224cfc_idx',
                               old_name='ui_queuedem_status_4eb84a_idx'),
        migrations.RenameField('queuedmessage', 'message', 'text'),
        # Defaults first, so migrating back to 0011 can re-add these columns
        # to rows that exist by then (their old values are gone either way)
        migrations.AlterField('queuedmessage', 'subject', models.CharField(max_length=255, default='')),
        migrations.AlterField('queuedmessage', 'from_email', models.CharField(max_length=255, default='')),
        migrations.AlterField('queuedmessage', 'recipient_list', models.JSONField(default=list)),
        migrations.RemoveField('queuedmessage', 'subject'),
        migrations.RemoveField('queuedmessage', 'html_message'),
        migrations.RemoveField('queuedmessage', 'from_email'),
        migrations.RemoveField('queuedmessage', 'recipient_list'),
        migrations.AddField(
            model_name='queuedmessage',
            name='chat_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
