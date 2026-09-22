"""Behavioural test for the vendored `pylons` compat package.

Not a smoke test: it exercises the parts r2 actually depends on --
verb-suffixed action dispatch, abort/redirect becoming responses, the
c_attach_args=False route-kwargs path, setting/getting attributes on c, and
teardown of the thread-local stack.
"""
import sys

sys.path.insert(0, '/Users/victor/src/Chatterly/r2')

failures = []


def check(label, condition, detail=""):
    print("  %-58s %s%s" % (label, "PASS" if condition else "FAIL",
                            "" if condition else "  <- " + str(detail)))
    if not condition:
        failures.append(label)


print("== import surface ==")
try:
    from pylons import (request, response, session, tmpl_context,
                        app_globals, translator, config, abort, redirect,
                        url_for, PylonsConfig)
    from pylons.controllers import WSGIController
    from pylons.controllers.util import abort as c_abort, redirect as c_redirect
    from pylons.i18n import _, ungettext, N_, get_lang
    from pylons.i18n.translation import (translation, LanguageError,
                                         NullTranslations, _get_translator)
    from pylons.middleware import ErrorHandler
    from pylons.wsgiapp import PylonsApp
    from pylons.error import handle_mako_error
    from pylons.util import PylonsContext, AttribSafeContextObj, ContextObj
    check("all 22 documented imports resolve", True)
except Exception as error:
    check("all 22 documented imports resolve", False, repr(error))
    raise SystemExit(1)

print("== configuration ==")
cfg = PylonsConfig()
cfg.init_app({'debug': 'false'}, {}, package='r2', paths={'templates': []})
cfg['pylons.strict_tmpl_context'] = False        # as environment.py does
cfg['pylons.c_attach_args'] = False              # as environment.py does
check("init_app set pylons.package", cfg['pylons.package'] == 'r2')
check("init_app set pylons.paths", 'templates' in cfg['pylons.paths'])
check("init_app set errorware", cfg['pylons.errorware'] == {})
check("response_options.headers is a dict",
      isinstance(cfg['pylons.response_options']['headers'], dict))
check("global config proxy sees it",
      config['pylons.package'] == 'r2' and config.get('pylons.package') == 'r2')


class Globals(object):
    media_domain = None
    def __init__(self):
        self.thing = 'globals-ok'
        self.cdn_provider = None


cfg['pylons.app_globals'] = Globals()

print("== c / g proxies ==")
c = tmpl_context          # r2 imports tmpl_context as c
ctx = AttribSafeContextObj()
check("AttribSafeContextObj returns '' for missing attr", ctx.not_set == '')
check("AttribSafeContextObj item access via dict", 'x' not in ctx)
ctx.x = 1
check("c attribute write/read via __dict__", ctx.x == 1 and ctx['x'] == 1)
check("plain ContextObj still missing-attrs-permissive via get()",
      ContextObj().get('nope') is None)

print("== controller dispatch ==")


class FrontController(WSGIController):
    def GET_index(self, **kw):
        c.menus = ['a', 'b']                      # writes onto tmpl_context
        return "index:%s:%s" % (app_globals.thing, c.menus)

    def POST_login(self, **kw):
        return "login:%s" % kw.get('dest', 'none')

    def GET_attrdemo(self):
        return "attr=%r" % (c.nonexistent,)       # AttribSafe -> ''

    def GET_boom(self):
        abort(403, "nope")

    def GET_redir(self):
        redirect('/elsewhere')

    def GET_whoami(self):
        return "ip=%s" % request.environ.get('REMOTE_ADDR')


class App(PylonsApp):
    def find_controller(self, name):
        return {'front': FrontController}[name]


app = App(config=cfg)


def call(method, action, extra=None):
    environ = {
        'REQUEST_METHOD': method,
        'PATH_INFO': '/',
        'QUERY_STRING': '',
        'SCRIPT_NAME': '',
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '80',
        'HTTP_HOST': 'localhost',
        'wsgi.url_scheme': 'http',
        'REMOTE_ADDR': '10.1.2.3',
        'pylons.routes_dict': {'controller': 'front', 'action': action},
    }
    if extra:
        environ.update(extra)
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured['status'] = status
        captured['headers'] = headers

    body = b''.join(app(environ, start_response))
    return captured.get('status'), body


status, body = call('GET', 'GET_index')
check("GET_index dispatched (verb-suffixed action)",
      status.startswith('200') and body == b"index:globals-ok:['a', 'b']",
      (status, body))

status, body = call('POST', 'POST_login', {'pylons.routes_dict': {
    'controller': 'front', 'action': 'POST_login', 'dest': '/x'}})
check("route kwargs reach the action (c_attach_args=False)",
      status.startswith('200') and body == b"login:/x", (status, body))

status, body = call('GET', 'GET_attrdemo')
check("c attribute miss yields '' (strict_tmpl_context False)",
      status.startswith('200') and body == b"attr=''", (status, body))

status, _unused = call('GET', 'GET_boom')
check("abort(403) becomes a 403 response",
      status.startswith('403'), status)

status, _unused = call('GET', 'GET_redir')
check("redirect() becomes a 302 response",
      status.startswith('302'), status)

status, body = call('GET', 'GET_whoami')
check("request is bound for the action",
      body == b"ip=10.1.2.3", body)

print("== stack teardown ==")
for proxy, name in ((request, 'request'), (response, 'response'),
                    (tmpl_context, 'tmpl_context'), (app_globals, 'app_globals'),
                    (translator, 'translator'), (session, 'session')):
    try:
        proxy._get_current_object()
        check("%s unbound after request" % name, False, "still bound")
    except RuntimeError:
        check("%s unbound after request" % name, True)

print("== i18n ==")
check("_() is identity with no translator bound", _("hello %(x)s", x=1) == "hello 1")
check("N_() marks without translating", N_("raw") == "raw")
check("ungettext pluralises off the bound translator",
      ungettext('one', 'many', 2) == 'many')
check("get_lang() returns None with nothing bound", get_lang() is None)
try:
    _get_translator('fr')
    check("_get_translator raises LanguageError with no catalogs", False)
except LanguageError:
    check("_get_translator raises LanguageError with no catalogs", True)
inert = _get_translator('fr', graceful_fail=True)
check("graceful_fail yields an inert translator",
      inert.gettext('x') == 'x')

print("== registry replace (what r2.lib.translation does) ==")
from pylons._registry import Registry

registry = Registry()
registry.register(translator, NullTranslations())
registry.replace(translator, inert)
check("registry.replace swaps the bound object",
      translator._get_current_object() is inert)
check("registry.get reads it back", registry.get(translator) is inert)
registry.pop()
try:
    translator._get_current_object()
    check("registry.pop unbinds", False)
except RuntimeError:
    check("registry.pop unbinds", True)

print()
if failures:
    print("FAILED: %d check(s): %s" % (len(failures), failures))
    raise SystemExit(1)
print("ALL CHECKS PASSED")
