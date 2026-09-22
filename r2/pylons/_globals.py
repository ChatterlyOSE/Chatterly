"""The per-thread Pylons proxies.

Kept in their own module (rather than in ``pylons/__init__.py``) so that
submodules such as ``pylons.controllers.util`` and ``pylons.i18n`` can import
the very same proxy instances without a circular import.
"""

from ._proxy import StackedObjectProxy

request = StackedObjectProxy('request')
response = StackedObjectProxy('response')
session = StackedObjectProxy('session')
tmpl_context = StackedObjectProxy('tmpl_context')
app_globals = StackedObjectProxy('app_globals')
translator = StackedObjectProxy('translator')
