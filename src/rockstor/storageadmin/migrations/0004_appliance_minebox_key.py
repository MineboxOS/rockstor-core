# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storageadmin', '0003_auto_20170114_1332'),
    ]

    operations = [
        migrations.AddField(
            model_name='appliance',
            name='minebox_key',
            field=models.CharField(max_length=255, null=True),
        ),
    ]
