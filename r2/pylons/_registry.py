"""A stand-in for ``paste.registry.Registry``.

Pylons consulted ``environ['paste.registry']`` to find the objects bound to a
proxy for the current request, and r2 relies on that directly::

    r2/r2/lib/translation.py:69
        registry = pylons.request.environ['paste.registry']
        ...
        registry.replace(pylons.translator, translator)

So the environ key and the ``replace`` call have to keep working.  Rather than
carry paste's registry machinery, this tracks the proxies bound for the
current request and delegates binding to them (they own the thread-local
stack), which also gives PylonsApp a single place to tear the request down.
"""


class Registry(object):
    def __init__(self):
        self._bound = []

    def register(self, proxy, obj):
        """Bind ``obj`` to ``proxy`` for this request."""
        proxy._push_object(obj)
        self._bound.append(proxy)
        return obj

    def replace(self, proxy, obj):
        """Rebind ``proxy`` to ``obj`` without changing the stack depth
        (paste.registry.Registry.replace)."""
        proxy._replace_object(obj)
        return obj

    def get(self, proxy, default=None):
        try:
            return proxy._get_current_object()
        except RuntimeError:
            return default

    def pop(self):
        """Unbind everything this registry bound, newest first."""
        while self._bound:
            self._bound.pop()._pop_object()
