"""``pylons.util`` — the slice of Pylons' util module that r2 imports.

r2/r2/lib/log.py imports::

    from pylons.util import PylonsContext, AttribSafeContextObj, ContextObj

and uses them purely as classes for isinstance() filtering when it scrubs
stack frames for logging, so they must exist and behave like the originals.
"""

from ._proxy import StackedObjectProxy
from .configuration import get_global_config

__all__ = ['StackedObjectProxy', 'ContextObj', 'AttribSafeContextObj',
           'PylonsContext', 'url_for', 'redirect_to']


class ContextObj(object):
    """The object behind ``c`` (``tmpl_context``).

    Both attribute- and item-addressable, backed by its own ``__dict__`` —
    the same contract as Pylons 1.0, which a lot of r2 code depends on
    (``c.menus = ...`` and ``c.get('x')`` both appear).
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __contains__(self, key):
        return key in self.__dict__

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value

    def __delitem__(self, key):
        del self.__dict__[key]

    def __iter__(self):
        return iter(self.__dict__)

    def __len__(self):
        return len(self.__dict__)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def keys(self):
        return self.__dict__.keys()

    def values(self):
        return self.__dict__.values()

    def items(self):
        return self.__dict__.items()

    def update(self, *args, **kwargs):
        self.__dict__.update(*args, **kwargs)

    def setdefault(self, *args, **kwargs):
        return self.__dict__.setdefault(*args, **kwargs)

    def pop(self, *args):
        return self.__dict__.pop(*args)

    def popitem(self):
        return self.__dict__.popitem()

    def clear(self):
        self.__dict__.clear()

    def has_key(self, key):  # py2 leftover, still called by some r2 code
        return key in self.__dict__

    def __repr__(self):
        return '<%s %r>' % (self.__class__.__name__, self.__dict__)


class AttribSafeContextObj(ContextObj):
    """A ContextObj that returns ``''`` for unknown attributes.

    r2 sets ``config['pylons.strict_tmpl_context'] = False`` in
    ``r2/config/environment.py`` precisely to get this behaviour ("when
    accessing non-existent attributes on c, return '' instead of dying"), and
    templates rely on it.
    """

    def __getattr__(self, name):
        # Dunder lookups must still fail loudly or copy/pickle get confused
        # into thinking the object implements protocols it does not.
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        return ''


class PylonsContext(object):
    """Per-request context holder (Pylons put request/response/c/g on one)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        return '<PylonsContext %r>' % (self.__dict__,)


def url_for(*args, **kwargs):
    """Build a URL from the application's Routes map."""
    config = get_global_config()
    mapper = config.get('routes.map') if config else None
    if mapper is None:
        raise RuntimeError("url_for called before the Routes map was built")
    return mapper.generate(**kwargs)


def redirect_to(url, **kwargs):
    from .controllers.util import redirect
    return redirect(url, **kwargs)
