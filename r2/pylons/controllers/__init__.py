"""``pylons.controllers`` — the WSGIController base class."""

from webob import exc

from .. import _globals


class WSGIController(object):
    """Runs ``__before__``, dispatches to the named action, runs ``__after__``,
    then turns the action's return value into an HTTP response.

    The action name comes from ``environ['pylons.routes_dict']['action']``.
    r2's ``BaseController.__call__`` (r2/lib/base.py:135) rewrites that key to
    include the HTTP verb before delegating here, so ``action='login'`` has
    already become ``'POST_login'`` by the time we look it up.
    """

    def __call__(self, environ, start_response):
        routes_dict = environ.get('pylons.routes_dict') or {}
        action = routes_dict.get('action') or environ.get('REQUEST_METHOD', 'GET')

        self.__before__()

        handler = getattr(self, action, None)
        if handler is None or not callable(handler):
            raise exc.HTTPNotFound("no action %r on %s"
                                   % (action, type(self).__name__))

        result = handler(**self._route_kwargs(routes_dict))

        self.__after__()

        return self._finish(environ, start_response, result)

    # -- hooks r2 overrides ----------------------------------------------
    def __before__(self):
        pass

    def __after__(self):
        pass

    # -- route parameters -------------------------------------------------
    def _route_kwargs(self, routes_dict):
        """The route match dict, minus Pylons' own bookkeeping keys, passed to
        the action as keyword arguments.

        Route defaults are intentional handler arguments in r2 (e.g.
        ``mc('/promoted/pay/:link/:campaign', ...)`` pairs with an action
        taking ``(self, link, campaign)``), so everything except
        controller/action/action_name is passed through.  Routes consumes
        ``requirements``/``conditions`` itself, so they never appear here.
        """
        params = {key: value for key, value in routes_dict.items()
                  if key not in ('controller', 'action', 'action_name')}

        if self._attach_route_args():
            # Pylons' pylons.c_attach_args=True put them on c instead.
            _globals.tmpl_context._get_current_object().update(params)
            return {}
        return params

    def _attach_route_args(self):
        try:
            from ..configuration import get_global_config
            config = get_global_config()
            if config is None:
                return False
            return bool(config.get('pylons.c_attach_args'))
        except Exception:
            return False

    # -- response construction -------------------------------------------
    def _finish(self, environ, start_response, result):
        from webob import Response

        response = _globals.response._get_current_object()

        if result is None:
            # The action already populated `response` (headers/body) itself.
            pass
        elif isinstance(result, Response):
            response = result
        elif isinstance(result, str):
            response.body = result.encode(response.charset or 'utf-8')
        elif isinstance(result, bytes):
            response.body = result
        elif callable(result):
            # A WSGI application returned directly.
            return result(environ, start_response)
        else:
            raise TypeError("controller %s returned unsupported %r"
                            % (type(self).__name__, result))

        return response(environ, start_response)
