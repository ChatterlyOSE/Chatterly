"""Python 3 stand-ins for the Paste 1.7.5 pieces r2 uses at runtime.

Paste 1.7.5 is Python-2-only and abandoned.  Its Python 3 successor,
PasteDeploy, provides *only* ``paste.deploy`` (app loading and config
converters) -- it does not carry the middleware modules r2 actually uses:

    paste.cascade          -> Cascade
    paste.errordocument    -> StatusBasedForward
    paste.recursive        -> RecursiveMiddleware
    paste.registry         -> RegistryManager, Registry, restorer
    paste.urlparser        -> StaticURLParser
    paste.request          -> path_info_split
    paste.util.mimeparse   -> parse_mime_type, desired_matches

Because PasteDeploy and Paste both claim the top-level ``paste`` package they
cannot be co-installed as a namespace package, so the four r2 modules that need
these import them from here instead.  PasteDeploy remains the source of
``paste.deploy.loadapp``/``asbool`` where r2 already used it.

Everything here is deliberately small and explicit: this stands in the request
path, so behaviour is spelled out rather than clever.
"""

import threading
from urllib.parse import urlsplit

from pylons._registry import Registry

__all__ = [
    'asbool', 'path_info_split', 'Cascade', 'StaticURLParser',
    'RegistryMiddleware', 'StatusBasedForward', 'RecursiveMiddleware',
    'Registry', 'restorer', 'parse_mime_type', 'desired_matches',
]


# --------------------------------------------------------------------------
# paste.deploy.converters
# --------------------------------------------------------------------------
def asbool(value):
    """Coerce a config value to a bool (paste.deploy.converters.asbool)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in ('true', 'yes', 'on', '1'):
        return True
    if text in ('false', 'no', 'off', '0', ''):
        return False
    raise ValueError("cannot coerce %r to a boolean" % (value,))


# --------------------------------------------------------------------------
# paste.request
# --------------------------------------------------------------------------
def path_info_split(path_info):
    """Split the first path segment off ``PATH_INFO``.

    '/domain/foo' -> ('domain', '/foo');  '/domain' -> ('domain', '').
    """
    if path_info.startswith('/'):
        path_info = path_info[1:]
    if '/' in path_info:
        first, rest = path_info.split('/', 1)
        return first, '/' + rest
    return path_info, ''


# --------------------------------------------------------------------------
# paste.util.mimeparse
# --------------------------------------------------------------------------
def parse_mime_type(mime_type):
    """Return ``(type, subtype, params)`` for a Content-Type value."""
    parts = (mime_type or '').split(';')
    full_type = parts[0].strip().lower()
    if '/' in full_type:
        type_, subtype = full_type.split('/', 1)
    else:
        type_, subtype = full_type, ''

    params = {}
    for param in parts[1:]:
        if '=' not in param:
            continue
        name, value = param.split('=', 1)
        name = name.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        params[name] = value
    return type_, subtype, params


def desired_matches(supported, header):
    """Subset of ``supported`` acceptable per an Accept-* header.

    gzipper only asks ``"gzip" in desired_matches(["gzip"], accept_encoding)``,
    so this covers type matching and ``*``, not full q-value ranking.
    """
    if not header:
        return []
    accepted = [part.strip().split(';')[0].strip().lower()
                for part in header.split(',')]
    return [item for item in supported
            if item.lower() in accepted or '*' in accepted]


# --------------------------------------------------------------------------
# paste.cascade
# --------------------------------------------------------------------------
class Cascade(object):
    """Run apps in order, falling through on 404 (paste.cascade.Cascade).

    r2 uses it to try the static-file app before the application.
    """

    def __init__(self, apps, catch=None):
        self.apps = list(apps)

    def __call__(self, environ, start_response):
        if not self.apps:
            raise RuntimeError("Cascade has no apps")

        for app in self.apps[:-1]:
            captured = {}

            def capture(status, headers, exc_info=None, _captured=captured):
                _captured['status'] = status
                _captured['headers'] = headers
                _captured['exc_info'] = exc_info

            app_iter = app(environ, capture)
            status = captured.get('status', '')

            if status.startswith('404'):
                close = getattr(app_iter, 'close', None)
                if close is not None:
                    close()
                continue

            start_response(captured['status'], captured['headers'],
                           captured.get('exc_info'))
            return app_iter

        return self.apps[-1](environ, start_response)


# --------------------------------------------------------------------------
# paste.urlparser
# --------------------------------------------------------------------------
class StaticURLParser(object):
    """Serve files from a directory (paste.urlparser.StaticURLParser).

    r2 only asks it to serve the built static assets ahead of the app; WebOb's
    directory application does exactly that, including the 404 that Cascade
    relies on to fall through.
    """

    def __init__(self, directory, root_path=None, cache_max_age=None, **kwargs):
        from webob.static import DirectoryApp
        self.directory = directory
        self.app = DirectoryApp(directory)

    def __call__(self, environ, start_response):
        return self.app(environ, start_response)


# --------------------------------------------------------------------------
# paste.registry
# --------------------------------------------------------------------------
class RegistryMiddleware(object):
    """Bind a Registry for the duration of each request.

    This is ``paste.registry.RegistryManager``'s job: Pylons' request/c/g
    proxies read their objects out of ``environ['paste.registry']``.  Our
    proxies own the per-thread stack, so this creates the registry, gives the
    app the chance to bind into it, and unbinds everything the request pushed
    once it is finished -- which is what keeps request state from leaking into
    the next one.
    """

    def __init__(self, app, *args, **kwargs):
        self.app = app

    def __call__(self, environ, start_response):
        registry = Registry()
        environ['paste.registry'] = registry
        try:
            return self.app(environ, start_response)
        finally:
            registry.pop()


class _Restorer(object):
    """Bookkeeping stand-in for ``paste.registry.restorer``.

    Paste used this to keep the special objects bound *between* requests in a
    long-lived process (``paster run``, the test harness).  With the proxies'
    stack here, the equivalent is done explicitly by pushing a Registry around
    the CLI/test body, so these record scope changes and nothing more.  They
    exist so callers that still reference the names keep working while they are
    migrated.
    """

    def __init__(self):
        self._local = threading.local()

    @property
    def _stack(self):
        try:
            return self._local.stack
        except AttributeError:
            self._local.stack = []
            return self._local.stack

    def restoration_begin(self, request_id=None):
        self._stack.append(request_id)

    def restoration_end(self):
        if self._stack:
            self._stack.pop()


restorer = _Restorer()


# --------------------------------------------------------------------------
# paste.errordocument / paste.recursive
# --------------------------------------------------------------------------
class StatusBasedForward(object):
    """Serve an error document for statuses the mapper maps.

    ``paste.errordocument.StatusBasedForward``.  r2 supplies ``error_mapper``
    (r2/config/middleware.py), which returns a relative URL such as
    ``/error/document/?code=404&...`` for the statuses it wants rendered, or
    None to leave the response alone.

    The forward re-enters the wrapped app with the error URL, and the *original*
    status is forced back onto the forwarded response -- a 404 must still be a
    404 after the error page renders.  Recursion is bounded by the mapper
    itself, which returns None when ``environ['pylons.error_call']`` is set.
    """

    def __init__(self, app, global_conf=None, mapper=None, **kwargs):
        self.app = app
        self.global_conf = global_conf or {}
        self.mapper = mapper
        self.kwargs = kwargs

    def __call__(self, environ, start_response):
        captured = {}

        def capture(status, headers, exc_info=None, _captured=captured):
            _captured['status'] = status
            _captured['headers'] = headers
            _captured['exc_info'] = exc_info

        app_iter = self.app(environ, capture)
        status = captured.get('status')
        if status is None:
            return app_iter

        code, _, message = status.partition(' ')
        try:
            code = int(code)
        except ValueError:
            start_response(status, captured['headers'], captured.get('exc_info'))
            return app_iter

        url = None
        if self.mapper is not None:
            url = self.mapper(code, message, environ, self.global_conf,
                              **self.kwargs)

        if not url:
            start_response(status, captured['headers'], captured.get('exc_info'))
            return app_iter

        close = getattr(app_iter, 'close', None)
        if close is not None:
            close()

        parts = urlsplit(url)
        forward_environ = environ.copy()
        forward_environ['PATH_INFO'] = parts.path
        forward_environ['QUERY_STRING'] = parts.query
        forward_environ['REQUEST_METHOD'] = 'GET'
        forward_environ['pylons.error_call'] = True
        forward_environ.pop('CONTENT_LENGTH', None)
        forward_environ.pop('CONTENT_TYPE', None)

        def forward_start_response(fwd_status, headers, exc_info=None):
            # The error document is a rendering of the original status.
            return start_response(status, headers, exc_info)

        return self.app(forward_environ, forward_start_response)


class RecursiveMiddleware(object):
    """``paste.recursive.RecursiveMiddleware`` in name only.

    Paste used it to expose ``environ['paste.recursive.forward']`` so error
    documents could re-enter the app.  StatusBasedForward above performs that
    forward itself (and ``pylons.error_call`` already bounds the recursion), so
    this is a pass-through kept so r2's stack construction is unchanged.
    """

    def __init__(self, app, *args, **kwargs):
        self.app = app

    def __call__(self, environ, start_response):
        return self.app(environ, start_response)
