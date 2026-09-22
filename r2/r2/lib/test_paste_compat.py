"""Behavioural test for the Paste 1.7.5 runtime replacements.

Exercises the pieces the request path actually depends on: Cascade falling
through on 404, StaticURLParser serving and 404ing, RegistryMiddleware binding
and unbinding a request's proxies, and StatusBasedForward serving an error
document while forcing the ORIGINAL status back onto the response.
"""
import os
import sys
import tempfile

sys.path.insert(0, '/Users/victor/src/Chatterly/r2')

failures = []


def check(label, condition, detail=""):
    print("  %-60s %s%s" % (label, "PASS" if condition else "FAIL",
                            "" if condition else "  <- " + str(detail)))
    if not condition:
        failures.append(label)


from r2.lib.paste_compat import (
    Cascade, RegistryMiddleware, Registry, StaticURLParser, StatusBasedForward,
    RecursiveMiddleware, asbool, path_info_split, parse_mime_type,
    desired_matches, restorer,
)


def call(app, path='/', query='', method='GET'):
    environ = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path,
        'QUERY_STRING': query,
        'SCRIPT_NAME': '',
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '80',
        'HTTP_HOST': 'localhost',
        'wsgi.url_scheme': 'http',
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured['status'] = status
        captured['headers'] = headers

    body = b''.join(app(environ, start_response))
    return captured.get('status'), body, environ


def simple(status, body):
    def app(environ, start_response):
        start_response(status, [('Content-Type', 'text/plain')])
        return [body]
    return app


print("== paste.deploy.converters.asbool ==")
check("asbool('true')", asbool('true') is True)
check("asbool('false')", asbool('false') is False)
check("asbool(None)", asbool(None) is False)
check("asbool('')", asbool('') is False)
try:
    asbool('nonsense')
    check("asbool rejects junk", False)
except ValueError:
    check("asbool rejects junk", True)

print("== paste.request.path_info_split ==")
check("/domain/foo -> ('domain', '/foo')",
      path_info_split('/domain/foo') == ('domain', '/foo'))
check("/domain -> ('domain', '')", path_info_split('/domain') == ('domain', ''))
check("'/a/b/c' -> ('a', '/b/c')", path_info_split('/a/b/c') == ('a', '/b/c'))

print("== paste.util.mimeparse ==")
check("parse_mime_type strips params",
      parse_mime_type('text/html; charset=UTF-8')
      == ('text', 'html', {'charset': 'UTF-8'}))
check("parse_mime_type plain",
      parse_mime_type('application/json') == ('application/json'.split('/')[0],
                                              'json', {}))
check("gzip detected in Accept-Encoding",
      'gzip' in desired_matches(['gzip'], 'gzip, deflate'))
check("gzip not matched for identity",
      'gzip' not in desired_matches(['gzip'], 'identity'))

print("== paste.cascade.Cascade ==")
cascade = Cascade([simple('404 Not Found', b'missing'),
                   simple('200 OK', b'from-app')])
status, body, _ = call(cascade, '/thing')
check("falls through a 404 to the next app",
      status.startswith('200') and body == b'from-app', (status, body))

cascade = Cascade([simple('200 OK', b'static'), simple('200 OK', b'app')])
status, body, _ = call(cascade, '/thing')
check("stops at the first non-404",
      status.startswith('200') and body == b'static', (status, body))

print("== paste.urlparser.StaticURLParser ==")
directory = tempfile.mkdtemp()
with open(os.path.join(directory, 'hello.txt'), 'w') as handle:
    handle.write('static-body')
static = StaticURLParser(directory)
status, body, _ = call(static, '/hello.txt')
check("serves an existing file",
      status.startswith('200') and body == b'static-body', (status, body))
status, body, _ = call(static, '/nope.txt')
check("404s a missing file (so Cascade can fall through)",
      status.startswith('404'), status)

print("== paste.registry.RegistryManager -> RegistryMiddleware ==")
from pylons import request as request_proxy


def inspect_app(environ, start_response):
    registry = environ['paste.registry']
    assert isinstance(registry, Registry)
    # RegistryMiddleware creates the registry; whoever runs inside (PylonsApp)
    # binds into it. Bind here the way PylonsApp would.
    registry.register(request_proxy, {'bound': 'inside'})
    assert request_proxy['bound'] == 'inside'
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b'ok']


wrapped = RegistryMiddleware(inspect_app)
status, body, environ = call(wrapped, '/')
check("registry is exposed to the app", body == b'ok', (status, body))
try:
    request_proxy._get_current_object()
    check("proxies unbound after the request", False, "still bound")
except RuntimeError:
    check("proxies unbound after the request", True)

print("== paste.errordocument.StatusBasedForward ==")


def mapper(code, message, environ, global_conf=None, **kw):
    # mirrors r2's error_mapper: render a document for 404, and never recurse
    if environ.get('pylons.error_call'):
        return None
    if code == 404:
        return '/error/document/?code=404'
    return None


def app_with_errors(environ, start_response):
    if environ['PATH_INFO'] == '/error/document/':
        start_response('200 OK', [('Content-Type', 'text/html')])
        return [b'<html>error page for %s</html>'
                % environ['QUERY_STRING'].encode()]
    start_response('404 Not Found', [('Content-Type', 'text/plain')])
    return [b'nothing here']


forwarder = RecursiveMiddleware(StatusBasedForward(app_with_errors,
                                                   {'debug': 'false'},
                                                   mapper))
status, body, _ = call(forwarder, '/missing')
check("error document body is served",
      b'error page' in body, body)
check("ORIGINAL 404 status is preserved through the forward",
      status.startswith('404'), status)

# a status the mapper does not map must pass through untouched
def pass_app(environ, start_response):
    start_response('201 Created', [('Content-Type', 'text/plain')])
    return [b'made']


forwarder = StatusBasedForward(pass_app, {}, mapper)
status, body, _ = call(forwarder, '/thing')
check("unmapped statuses pass through unchanged",
      status.startswith('201') and body == b'made', (status, body))

print("== paste.registry.restorer (name compatibility) ==")
restorer.restoration_begin(7)
restorer.restoration_end()
check("restorer begin/end are callable", True)

print("== registry spans the middleware chain ==")
# This is the r2 stack's real shape: RegistryMiddleware wraps the app, and
# error_mapper (outside PylonsApp) reads `c` AFTER the inner app has returned.
# If PylonsApp popped the registry itself, that read would blow up.
from pylons import PylonsConfig, tmpl_context as c_proxy
from pylons.controllers import WSGIController
from pylons.wsgiapp import PylonsApp

cfg = PylonsConfig()
cfg.init_app({'debug': 'false'}, {}, package='r2', paths={})
cfg['pylons.strict_tmpl_context'] = False
cfg['pylons.c_attach_args'] = False
cfg['pylons.app_globals'] = object()


class Ctrl(WSGIController):
    def GET_index(self):
        c_proxy.allow_framing = 1
        return "ok"


class App(PylonsApp):
    def find_controller(self, name):
        return Ctrl


observed = {}


def outer(environ, start_response):
    inner = App(config=cfg)
    environ['pylons.routes_dict'] = {'controller': 'front', 'action': 'GET_index'}
    body = b''.join(inner(environ, start_response))
    # what error_mapper does, outside PylonsApp
    observed['allow_framing'] = c_proxy.allow_framing
    return [body]


status, body, _ = call(RegistryMiddleware(outer), '/')
check("c is still bound after the inner app returns",
      observed.get('allow_framing') == 1, observed)
check("inner response served through the chain", body == b'ok', body)
try:
    c_proxy._get_current_object()
    check("c unbound once RegistryMiddleware finishes", False, "still bound")
except RuntimeError:
    check("c unbound once RegistryMiddleware finishes", True)

print()
if failures:
    print("FAILED: %d check(s): %s" % (len(failures), failures))
    raise SystemExit(1)
print("ALL CHECKS PASSED")
