# -*- coding: utf-8 -*-
# Generated by Django 1.11.23 on 2019-09-02 22:02
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('ws', '0017_participant_insecure_password')]

    operations = [
        migrations.AddField(
            model_name='participant',
            name='password_last_checked',
            field=models.DateTimeField(
                null=True,
                blank=True,
                verbose_name="Last time password was checked against HaveIBeenPwned's database",
            ),
        )
    ]
