"""
This module contains celery tasks for massadmin
"""
import sys

from django.core.exceptions import ValidationError

from django.contrib import admin
from django.db import transaction
from django.contrib.admin import helpers
from django.forms.formsets import all_valid
try:
    from django.contrib.admin.utils import unquote
except ImportError:
    from django.contrib.admin.util import unquote

from celery import shared_task
from celery.utils.log import get_task_logger

import json
from django.apps import apps

from uh_core.admin_access.admin_site import BaseAdminSite

logger = get_task_logger(__name__)

def get_formsets(model, request, obj=None):
    try:  # Django>=1.9
        return [f for f, _ in model.get_formsets_with_inlines(request, obj)]
    except AttributeError:
        return model.get_formsets(request, obj)


@shared_task()
def mass_edit(comma_separated_object_ids, serialized_queryset, post, files, mass_changes_fields, admin_site_name):
    """
    Edits queryset asynchronously.

    comma_separated_object_ids  - Object ids
    serialized_queryset         - Queryset Turned into json
    post                        - Request post data
    files                       - Request files data
    mass_changes_fields         - Fields selected for mass change
    """

    serialized_queryset = json.loads(serialized_queryset)
    object_ids = comma_separated_object_ids.split(',')
    object_id = object_ids[0]
    formsets = []

    model = serialized_queryset[0]["model"].split(".")
    model = apps.get_model(model[0], model[1])
    queryset = model.objects.all()

    obj = queryset.get(pk=unquote(object_id))
    errors, errors_list = None, None

    admin_site = BaseAdminSite(name=admin_site_name)

    admin_model = admin.ModelAdmin(model, admin_site)

    ModelForm = admin_model.get_form(None, obj)

    try:
        with transaction.atomic():
            objects_count = 0
            changed_count = 0
            objects = queryset.filter(pk__in=object_ids)
            for obj in objects:
                objects_count += 1
                form = ModelForm(
                    post,
                    files,
                    instance=obj)

                # refresh InMemoryUploadedFile object.
                # It should not cause memory leaks as it
                # only fseeks to the beggining of the media file.
                for in_memory_file in files.values():
                    in_memory_file.open()

                exclude = []
                for fieldname, field in list(form.fields.items()):
                    if fieldname not in mass_changes_fields:
                        exclude.append(fieldname)

                for exclude_fieldname in exclude:
                    del form.fields[exclude_fieldname]

                if form.is_valid():
                    form_validated = True
                    new_object = form.save()
                else:
                    form_validated = False
                    new_object = obj
                prefixes = {}

                # Adds a prefix to all formsets
                for FormSet in get_formsets(admin_model, None, new_object): #request
                    prefix = FormSet.get_default_prefix()
                    prefixes[prefix] = prefixes.get(prefix, 0) + 1
                    if prefixes[prefix] != 1:
                        prefix = "%s-%s" % (prefix, prefixes[prefix])
                    if prefix in mass_changes_fields:
                        formset = FormSet(
                            post,
                            files,
                            instance=new_object,
                            prefix=prefix)
                        formsets.append(formset)

                if all_valid(formsets) and form_validated:
                    new_object.save()
                    form.save_m2m()
                    for formset in formsets:
                        formset.save()
                    changed_count += 1

            if changed_count != objects_count:
                errors = form.errors
                errors_list = helpers.AdminErrorList(form, formsets)
                logger.error(errors)
                logger.error(errors_list)

                # Raise error for rollback transaction in atomic block
                raise ValidationError("Not all forms is correct")

    except Exception:
        general_error = sys.exc_info()[1]
        logger.error(general_error)