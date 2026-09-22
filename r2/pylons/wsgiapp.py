"""``pylons.wsgiapp`` — PylonsApp, the controller-dispatching WSGI app.

``r2/config/middleware.py`` subclasses it::

    class RedditApp(PylonsApp):
        def setup_app_env(self, environ, start_response): ...   # + load controllers
        def find_controller(self, controller_name): ...          # + plugin controllers

and builds ``RedditApp(config=config)``.  So the contract this must honour is
narrow and exact: ``__init__(config=...)``, ``setup_app_env(environ,
start_response)``, ``find_controller(name)``, plus a ``controller_classes``
cache that RedditApp reads and writes.

Where Pylons used ``paste.registry`` to bind the request objects, this uses the
``Registry`` in ``pylons._registry`` — same ``environ['paste.registry']`` key,
same ``replace()`` call, so ``r2.lib.translation`` is unaffected.
"""

import importlib

from webob import Request as _WebObRequest
from webob import Response as _WebObResponse
from webob import exc

from . import _globals
from ._registry import Registry
from .configuration import get_global_config
from .i18n.translation import NullTranslations
from .util import AttribSafeContextObj, ContextObj


class Request(_WebObRequest):
    """WebOb's Request plus the attributes Pylons put on it.

    r2 assigns a lot of its own attributes directly (``request.ip``,
    ``request.via_cdn``, ``request.parsed_agent`` ...), which works because
    WebOb's Request is an ordinary object; these defaults just cover the ones
    Pylons pre-declared.
    """

    locale = None
    language = None
    via_cdn = False


class Response(_WebObResponse):
    """WebOb's Response plus the streamed ``write()`` Pylons callers expect."""

    def write(self, text):
        if isinstance(text, str):
            text = text.encode(self.charset or 'utf-8')
        self.body = (self.body or b'') + text


class Session(dict):
    """Minimal session object.

    Pylons' session came from Beaker middleware.  r2 imports ``session`` from
    pylons in exactly one module (r2/lib/base.py) and does its real session
    work in ``r2.lib.session``, so a dict-compatible stand-in is enough for the
    import surface.  If Beaker does put ``environ['beaker.session']`` in place,
    that object is used instead.
    """

    created = 0
    accessed = 0

    def save(self):
        pass

    def delete(self):
        pass

    def invalidate(self):
        pass

    def persist(self):
        pass

    def is_new(self):
        return True


class PylonsApp(object):
    def __init__(self, config=None, package_name=None, **kwargs):
        self.config = config if config is not None else get_global_config()
        self.package_name = package_name or self.config.get('pylons.package')
        self.controller_classes = {}
        self.request_options = dict(self.config.get('pylons.request_options') or {})
        self.response_options = dict(self.config.get('pylons.response_options') or {})

    # -- per-request environment -----------------------------------------
    def setup_app_env(self, environ, start_response):
        config = self.config

        request = Request(environ)

        options = self.response_options
        response = Response()
        if options.get('content_type'):
            response.content_type = options['content_type']
        if options.get('charset'):
            response.charset = options['charset']
        headers = options.get('headers') or {}
        header_items = headers.items() if hasattr(headers, 'items') else headers
        for name, value in header_items:
            response.headers[str(name)] = str(value)

        strict = config.get('pylons.strict_tmpl_context', True)
        tmpl_context = ContextObj() if strict else AttribSafeContextObj()

        session = environ.get('beaker.session')
        if session is None:
            session = Session()

        registry = environ.get('paste.registry')
        owned = registry is None
        if owned:
            registry = Registry()
            environ['paste.registry'] = registry
        # The registry may be owned by RegistryMiddleware (the real stack) or
        # by us (standalone use / tests).  Only the owner may tear it down --
        # see __call__.
        environ['pylons._own_registry'] = owned

        registry.register(_globals.request, request)
        registry.register(_globals.response, response)
        registry.register(_globals.tmpl_context, tmpl_context)
        registry.register(_globals.session, session)
        registry.register(_globals.translator, NullTranslations())

        app_globals = config.get('pylons.app_globals')
        if app_globals is not None:
            registry.register(_globals.app_globals, app_globals)

        environ['pylons.request_options'] = self.request_options
        environ['pylons.response_options'] = options
        return environ

    # -- dispatch ---------------------------------------------------------
    def __call__(self, environ, start_response):
        self.setup_app_env(environ, start_response)
        registry = environ['paste.registry']
        try:
            routes_dict = environ.get('pylons.routes_dict')
            if not routes_dict:
                routes_dict = self._match_routes(environ)

            controller_name = routes_dict.get('controller')
            if not controller_name:
                raise exc.HTTPNotFound("no controller matched %r"
                                       % (environ.get('PATH_INFO'),))

            controller = self.find_controller(controller_name)()
            return controller(environ, start_response)
        except exc.HTTPException as http_error:
            # abort()/redirect() raise these; they are responses, not errors.
            return http_error(environ, start_response)
        finally:
            # RegistryMiddleware owns the registry in the real stack and
            # unbinds at the end of the request; only pop if we created it.
            if environ.pop('pylons._own_registry', False):
                registry.pop()

    def _match_routes(self, environ):
        mapper = self.config.get('routes.map')
        if mapper is None:
            raise exc.HTTPNotFound("no routes map configured")
        match = mapper.match(environ.get('PATH_INFO', '/'), environ)
        if not match:
            raise exc.HTTPNotFound()
        environ['pylons.routes_dict'] = match
        return match

    def find_controller(self, controller_name):
        """Resolve a controller name to its class.

        r2's RedditApp overrides this to consult its own controller registry;
        this default handles standalone use.
        """
        if controller_name in self.controller_classes:
            return self.controller_classes[controller_name]

        module = importlib.import_module(self.package_name + '.controllers')
        controller_class = module.get_controller(controller_name)
        self.controller_classes[controller_name] = controller_class
        return controller_class
