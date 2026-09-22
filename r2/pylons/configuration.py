"""``pylons.configuration`` — the config object and the global ``config``.

r2 builds this in ``r2/config/environment.py``::

    config = PylonsConfig()
    config.init_app(global_conf, app_conf, package='r2', paths=paths)
    config['pylons.app_globals'] = Globals(...)

and then reads a fixed set of ``pylons.*`` keys out of it (``pylons.package``,
``pylons.paths``, ``pylons.h``, ``pylons.errorware``, ``pylons.response_options``,
``pylons.c_attach_args``, ``pylons.strict_tmpl_context``).  Those defaults are
reproduced here so the app's own assignments are the only thing that matters.
"""

_global_config = None


def set_global_config(config):
    global _global_config
    _global_config = config
    return config


def get_global_config():
    return _global_config


class PylonsConfig(dict):
    """Dict of configuration, plus Pylons' ``init_app`` bootstrap."""

    def init_app(self, global_conf, app_conf, package=None, paths=None):
        # Pylons folded the app's own conf into the config dict.
        self.update(global_conf or {})
        self.update(app_conf or {})

        self['pylons.environ_config'] = {
            'session': 'beaker.session',
            'cache': 'beaker.cache',
        }
        self['pylons.request_options'] = {}
        self['pylons.response_options'] = {
            'content_type': 'text/html',
            'charset': 'utf-8',
            'headers': {},
            'errors': 'strict',
        }
        self['pylons.strict_tmpl_context'] = True
        self['pylons.package'] = package
        self['pylons.paths'] = paths or {}
        self['pylons.c_attach_args'] = True
        self['pylons.h'] = None
        self['pylons.g'] = None
        self['pylons.errorware'] = {}
        # The app replaces this with its own Globals a few lines later; a
        # placeholder keeps `config['pylons.app_globals']` from being a KeyError
        # if anything reads it early.
        self['pylons.app_globals'] = None
        self['routes.map'] = None

        set_global_config(self)
        return self


class _GlobalConfigProxy(object):
    """``from pylons import config``.

    Pylons' ``config`` is global rather than per-request, so this simply
    forwards to whatever ``PylonsConfig`` was installed by ``init_app``.
    """

    def _cfg(self):
        config = get_global_config()
        if config is None:
            raise RuntimeError(
                "pylons.config was read before PylonsConfig.init_app() ran")
        return config

    def __getitem__(self, key):
        return self._cfg()[key]

    def __setitem__(self, key, value):
        self._cfg()[key] = value

    def __delitem__(self, key):
        del self._cfg()[key]

    def __contains__(self, key):
        return key in self._cfg()

    def __iter__(self):
        return iter(self._cfg())

    def __len__(self):
        return len(self._cfg())

    def get(self, key, default=None):
        return self._cfg().get(key, default)

    def keys(self):
        return self._cfg().keys()

    def values(self):
        return self._cfg().values()

    def items(self):
        return self._cfg().items()

    def update(self, *args, **kwargs):
        return self._cfg().update(*args, **kwargs)

    def setdefault(self, *args, **kwargs):
        return self._cfg().setdefault(*args, **kwargs)

    def __getattr__(self, attr):
        config = self._cfg()
        try:
            return config[attr]
        except KeyError:
            raise AttributeError(attr)

    def __repr__(self):
        return '<pylons.config %r>' % (get_global_config(),)


config = _GlobalConfigProxy()
