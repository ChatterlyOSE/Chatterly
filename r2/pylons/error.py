"""``pylons.error`` — the Mako error handler hook.

``r2/config/environment.py`` passes this to Mako:

    TemplateLookup(..., error_handler=handle_mako_error, ...)

Mako invokes it with ``(context, exception)`` when a template raises.  Pylons
attached the Mako-level traceback to the exception so the debug error page
could point at the failing template line, then re-raised; the re-raise is the
part that actually matters to the caller, so that is what is preserved here.
"""

import sys


def handle_mako_error(context, error):
    """Re-raise a template exception, carrying Mako's own traceback."""
    try:
        from mako.exceptions import RichTraceback
    except ImportError:
        pass
    else:
        try:
            error.mako_traceback = RichTraceback()
        except Exception:
            pass

    raise error
