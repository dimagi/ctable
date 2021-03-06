import inspect
import logging
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS
from django.db.models import signals
from importlib import import_module
from ctable.fixtures import CtableMappingFixture
from fluff.signals import indicator_document_updated, BACKEND_COUCH

logger = logging.getLogger(__name__)


def process_fluff_diff(sender, diff=None, backend=None, **kwargs):
    if not diff or backend != BACKEND_COUCH:
        return

    from ctable.util import get_extractor
    from .util import get_backend_name_for_fluff_pillow
    backend_name = get_backend_name_for_fluff_pillow(diff['doc_type'])
    if diff and backend_name:
        get_extractor(backend_name).process_fluff_diff(diff, backend_name)

indicator_document_updated.connect(process_fluff_diff)


def catch_signal(sender, **kwargs):
    if settings.UNIT_TESTING or kwargs['using'] != DEFAULT_DB_ALIAS:
        return

    app_name = sender.name
    try:
        mod = import_module('.ctable_mappings', app_name)
        print "Creating CTable mappings for %s" % app_name

        clsmembers = inspect.getmembers(mod, inspect.isclass)
        mappings = [cls[1] for cls in clsmembers
                    if not cls[1] == CtableMappingFixture and issubclass(cls[1], CtableMappingFixture)]
        for mapping in mappings:
            try:
                mapping().create()
            except Exception as e:
                logging.error('Unable to create mapping %s' % mapping)
                raise
    except ImportError:
        pass


signals.post_migrate.connect(catch_signal)
