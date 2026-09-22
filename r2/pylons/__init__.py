"""Pylons 1.0 API compatibility layer for Python 3.

r2 is written against Pylons 1.0, which is Python-2-only and unmaintained, and
against Paste 1.7.5, which is likewise dead.  Rather than rewrite the framework
idioms by hand across ~179 modules (plus the plugin repos and the Mako template
imports in ``r2/config/environment.py``), this package reimplements the small
slice of Pylons' public API that r2 actually uses, on top of **WebOb** and
**Routes** — both of which have current, maintained Python 3 releases.

It is vendored as a top-level ``pylons`` package on purpose, so that

    from pylons import app_globals as g
    from pylons import tmpl_context as c

keeps working byte-for-byte.  The real Pylons cannot be installed on Python 3
(v1.0.1's setup.py is Python 2 source), so this shadows nothing.

The surface implemented here was derived from the tree, not guessed.  The
complete set of Pylons imports in r2/ and scripts/ is:

    from pylons import app_globals as g        (160 modules)
    from pylons import tmpl_context as c        (82)
    from pylons import request                  (55)
    from pylons import response                 (15)
    from pylons import config
    from pylons import session                  (1)
    from pylons import url_for
    from pylons.controllers import WSGIController
    from pylons.controllers.util import abort, redirect
    from pylons.i18n import _, N_, ungettext, get_lang
    from pylons.i18n.translation import translation, LanguageError, \
        NullTranslations, _get_translator
    from pylons.middleware import ErrorHandler
    from pylons.wsgiapp import PylonsApp
    from pylons.error import handle_mako_error
    from pylons.configuration import PylonsConfig
    from pylons.util import PylonsContext, AttribSafeContextObj, ContextObj
"""

from ._proxy import StackedObjectProxy
from ._globals import (request, response, session, tmpl_context,
                       app_globals, translator)
from .configuration import PylonsConfig, config
from .util import url_for, redirect_to, ContextObj, AttribSafeContextObj
from .controllers.util import abort, redirect

__all__ = [
    'request', 'response', 'session', 'tmpl_context', 'app_globals',
    'translator', 'config', 'abort', 'redirect', 'url_for', 'redirect_to',
    'StackedObjectProxy', 'PylonsConfig', 'ContextObj',
    'AttribSafeContextObj',
]
