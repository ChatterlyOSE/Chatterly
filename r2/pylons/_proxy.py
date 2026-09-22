"""Thread-local stacked-object proxies.

A Python 3 replacement for Pylons 1.0's ``pylons.util.StackedObjectProxy``.

Pylons backed its proxies (``request``, ``c``, ``g``, ...) with
``paste.registry``.  Paste has no Python 3 release, so this implements the same
idea on a plain ``threading.local`` stack.  The public surface deliberately
matches Pylons' because the r2 test suite calls ``_push_object`` directly:

    r2/r2/tests/__init__.py:
        pylons.app_globals._push_object(wsgiapp.config['pylons.app_globals'])
"""

import threading


class StackedObjectProxy(object):
    def __init__(self, name):
        object.__setattr__(self, '_name', name)
        object.__setattr__(self, '_local', threading.local())

    # -- the thread-local stack ------------------------------------------
    @property
    def _stack(self):
        local = object.__getattribute__(self, '_local')
        try:
            return local.stack
        except AttributeError:
            local.stack = []
            return local.stack

    def _push_object(self, obj):
        self._stack.append(obj)
        return obj

    def _pop_object(self):
        stack = self._stack
        if not stack:
            raise RuntimeError("cannot pop %r: stack is empty on this thread"
                               % object.__getattribute__(self, '_name'))
        return stack.pop()

    def _replace_object(self, obj):
        """Swap the current binding.

        This is what ``Registry.replace(proxy, obj)`` means, and it is how
        r2.lib.translation.swap_lang/``set_lang`` installs a translator for the
        current request.
        """
        stack = self._stack
        if stack:
            stack[-1] = obj
        else:
            stack.append(obj)
        return obj

    def _get_current_object(self):
        stack = self._stack
        if not stack:
            raise RuntimeError("no object bound to %r on this thread"
                               % object.__getattribute__(self, '_name'))
        return stack[-1]

    # -- attribute delegation -------------------------------------------
    # __getattr__ only fires on a miss, so the proxy's own internals
    # (_name, _local, _stack, the methods above) still resolve normally.
    def __getattr__(self, attr):
        return getattr(self._get_current_object(), attr)

    def __setattr__(self, attr, value):
        if attr.startswith('_'):
            object.__setattr__(self, attr, value)
        else:
            setattr(self._get_current_object(), attr, value)

    def __delattr__(self, attr):
        if attr.startswith('_'):
            object.__delattr__(self, attr)
        else:
            delattr(self._get_current_object(), attr)

    # -- container / call / comparison delegation ------------------------
    def __getitem__(self, key):
        return self._get_current_object()[key]

    def __setitem__(self, key, value):
        self._get_current_object()[key] = value

    def __delitem__(self, key):
        del self._get_current_object()[key]

    def __contains__(self, item):
        return item in self._get_current_object()

    def __iter__(self):
        return iter(self._get_current_object())

    def __len__(self):
        return len(self._get_current_object())

    def __call__(self, *args, **kwargs):
        return self._get_current_object()(*args, **kwargs)

    def __enter__(self):
        return self._get_current_object().__enter__()

    def __exit__(self, *exc_info):
        return self._get_current_object().__exit__(*exc_info)

    def __bool__(self):
        return bool(self._get_current_object())

    def __eq__(self, other):
        return self._get_current_object() == other

    def __ne__(self, other):
        return self._get_current_object() != other

    def __hash__(self):
        return hash(self._get_current_object())

    def __str__(self):
        return str(self._get_current_object())

    def __repr__(self):
        try:
            current = self._get_current_object()
        except RuntimeError:
            return '<StackedObjectProxy %r (unbound)>' % (
                object.__getattribute__(self, '_name'),)
        return '<StackedObjectProxy %r -> %r>' % (
            object.__getattribute__(self, '_name'), current)
