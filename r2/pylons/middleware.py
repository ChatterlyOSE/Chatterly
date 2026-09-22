"""``pylons.middleware`` — ErrorHandler.

``r2/config/middleware.py`` builds it as::

    app = ErrorHandler(app, global_conf, **config['pylons.errorware'])

so the signature must accept an arbitrary ``**errorware``.  In r2's ini that
dict is empty, so this falls back to its own handling.

HTTP exceptions are deliberately *re-raised*: they are not errors to swallow
but statuses (401/403/404/429/...) that the error-document middleware sitting
outside this one is responsible for turning into a page.  Only genuine
unhandled exceptions are converted here, and only when not in debug mode.
"""

import logging

from webob import exc

from .configuration import get_global_config

log = logging.getLogger('pylons.error')


def asbool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class ErrorHandler(object):
    def __init__(self, app, global_conf=None, **errorware):
        self.app = app
        self.global_conf = global_conf or {}
        self.errorware = errorware
        self.debug = asbool(self.global_conf.get('debug'))

    def __call__(self, environ, start_response):
        try:
            return self.app(environ, start_response)
        except exc.HTTPException:
            raise
        except Exception:
            if self.debug:
                raise
            return self._handle_error(environ, start_response)

    def _handle_error(self, environ, start_response):
        log.exception("Unhandled exception serving %s",
                      environ.get('PATH_INFO', '?'))

        error_handler = self.errorware.get('error_handler')
        if error_handler is not None:
            try:
                return error_handler(environ, start_response)
            except exc.HTTPException as http_error:
                return http_error(environ, start_response)

        response = exc.HTTPInternalServerError()
        return response(environ, start_response)
