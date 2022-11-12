"""
This module contains celery tasks for massadmin
"""
try:
    from django.contrib.admin.utils import unquote
except ImportError:
    from django.contrib.admin.util import unquote

from celery import shared_task

try:  # Django>=1.9
    from django.apps import apps
    get_model = apps.get_model
except ImportError:
    from django.db.models import get_model

from django.db import transaction


@shared_task()
def mass_edit(object_ids, app_name, model_name, mass_changes_fields, temp_object_id):
    """
    Edits queryset asynchronously.

    comma_separated_object_ids  - List of all objects selected by the user separated by commas
    app_name                    - Django app name for model
    model_name                  - django model name for model
    mass_changes_fields         - Fields selected for mass change
    temp_object_id              - Object containing all edited fields listed in mass_changes_fields
    """

    object_ids = object_ids.split(',')
    object_ids.remove(temp_object_id)

    model = get_model(app_name, model_name)
    queryset = model.objects.all()

    temp_object = queryset.get(pk=unquote(temp_object_id))

    temp_data = {}

    for field in mass_changes_fields:
        temp_data[field] = getattr(temp_object, field)

    # Maybe we should split the atomic transaction into multiple requests, to not block the database
    # It is a simple query but the impact might be big

    # Atomic's exception should be handled, but until we figure
    # out the tier3 mailing
    with transaction.atomic():
        queryset.filter(pk__in=object_ids).update(**temp_data)

        # I can't find a reliable way to notify the user
        # when transaction ends, as it would require usage
        # of objects from tier3, which is impossible from module's point
