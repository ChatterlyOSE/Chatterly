"""``pylons.i18n`` — the gettext entry points r2 imports.

r2 modules do::

    from pylons.i18n import N_, _, ungettext, get_lang

(``_`` x44 modules, ``ungettext`` x10, ``N_`` x9, ``get_lang`` x1).  They are
bound to the ``pylons.translator`` proxy, which PylonsApp binds per request.

Unlike Pylons, these degrade instead of exploding when no translator is bound
(no request, or a module-level ``_("...")`` evaluated at import time): the
message is returned unchanged, which is exactly what ``NullTranslations``
would have done anyway.
"""

from .._globals import translator
from .translation import (LanguageError, NullTranslations, translation,
                          _get_translator)

__all__ = ['_', 'N_', 'ungettext', 'gettext', 'ngettext', 'get_lang', 'set_lang',
           'language', 'LanguageError', 'NullTranslations', 'translation',
           '_get_translator']


def _current_translator():
    try:
        return translator._get_current_object()
    except RuntimeError:
        return None


def _(value, *args, **kwargs):
    """Translate ``value`` (gettext), with optional interpolation."""
    current = _current_translator()
    if current is None:
        message = value
    else:
        try:
            return current.gettext(value, *args, **kwargs)
        except TypeError:
            message = current.gettext(value)

    if kwargs:
        return message % kwargs
    if args:
        return message % args
    return message


def gettext(value, *args, **kwargs):
    return _(value, *args, **kwargs)


def ungettext(singular, plural, n, *args, **kwargs):
    """Plural-aware translation (ngettext)."""
    current = _current_translator()
    if current is None:
        message = singular if n == 1 else plural
    else:
        try:
            return current.ngettext(singular, plural, n, *args, **kwargs)
        except (AttributeError, TypeError):
            message = singular if n == 1 else plural

    if kwargs:
        return message % kwargs
    if args:
        return message % args
    return message


def ngettext(singular, plural, n, *args, **kwargs):
    return ungettext(singular, plural, n, *args, **kwargs)


def N_(value):
    """Mark a string for extraction without translating it now."""
    return value


def get_lang():
    """The language code(s) of the translator bound to this request."""
    current = _current_translator()
    return getattr(current, 'pylons_lang', None)


def set_lang(lang, **kwargs):
    """Rebind the request's translator.

    r2 does its own version of this in ``r2/lib/translation.py`` (which needs
    the base/dialect and fallback handling); this covers direct callers.
    """
    from .._registry import Registry

    try:
        from .._globals import request
        registry = request.environ['paste.registry']
        assert isinstance(registry, Registry)
    except (RuntimeError, KeyError, AssertionError):
        registry = None

    new_translator = (_get_translator(lang, graceful_fail=True, **kwargs)
                      if lang else NullTranslations())

    if registry is not None:
        registry.replace(translator, new_translator)
    else:
        translator._replace_object(new_translator)
    return new_translator


#: Pylons exposed the current language as a module attribute too.
def language():
    return get_lang()
