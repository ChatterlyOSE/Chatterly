"""``pylons.controllers.util`` — ``abort()`` and ``redirect()``.

Imported by r2 in eight modules::

    from pylons.controllers.util import abort
    from pylons.controllers.util import redirect

``abort`` accepts what Pylons accepted: an integer status code, an
``HTTPException`` class, or an ``HTTPException`` instance.  r2/lib/base.py
defines its own richer ``abort`` on top of these; the other call sites use
this one directly.
"""

from webob import exc


def abort(status_code=None, detail="", headers=None, comment=None, **kwargs):
    """Raise the HTTP exception for ``status_code``."""
    if status_code is None:
        status_code = 500

    if isinstance(status_code, exc.HTTPException):
        raise status_code

    if isinstance(status_code, type) and issubclass(status_code, exc.HTTPException):
        raise status_code(detail=detail, headers=headers, comment=comment,
                          **kwargs)

    try:
        exception_class = exc.status_map[status_code]
    except KeyError:
        raise ValueError("no HTTP exception for status %r" % (status_code,))

    raise exception_class(detail=detail, headers=headers, comment=comment,
                          **kwargs)


def redirect(url, **kwargs):
    """Raise a 302 redirect to ``url`` (Pylons ended the request here).

    WebOb's HTTPFound is itself a WSGI app, so the raising controller's
    ``PylonsApp.__call__`` catches it and serves the response.
    """
    raise exc.HTTPFound(location=url, **kwargs)
