"""``pylons.i18n.translation`` — gettext catalog loading.

r2 layers its own module on top of this (``r2/lib/translation.py``)::

    from pylons.i18n.translation import translation, LanguageError, \\
        NullTranslations

and the test package imports ``_get_translator`` from here directly
(``r2/r2/tests/__init__.py:31``), which is why all four names must exist.

``r2.lib.translation._get_translator`` treats an ``IOError`` out of
``translation(...)`` as "this locale has no catalogs" and falls back to
``NullTranslations``.  With no ``reddit_i18n`` package installed the locale
directory is empty, so that is the path actually taken — and keeping the
IOError contract intact is what makes an untranslated install work.

Note: nothing here may be named ``gettext`` at module level, or the
``import gettext`` at the top of this file would be shadowed and every
``gettext.NullTranslations`` reference below would break at call time.
"""

import gettext

__all__ = ['LanguageError', 'NullTranslations', 'translation',
           '_get_translator']


class LanguageError(Exception):
    """Raised when a translation catalog cannot be loaded."""


#: The stdlib's NullTranslations is what Pylons re-exported.
NullTranslations = gettext.NullTranslations


class translation(gettext.NullTranslations):
    """A loadable gettext catalog.

    Constructed with a domain and a locale directory; raises ``IOError`` when
    there is no catalog for any of the requested languages.  The loaded
    ``GNUTranslations`` is delegated to for actual lookups, and
    ``gettext``/``ngettext`` accept Babel-style interpolation keywords, which
    is how r2 calls them::

        _("you must be %(name)s to do that", name=name)
    """

    def __init__(self, domain, localedir=None, languages=None, **kwargs):
        gettext.NullTranslations.__init__(self)

        self.domain = domain
        self.localedir = localedir

        if languages is None:
            languages = []
        elif not isinstance(languages, (list, tuple)):
            languages = [languages]
        self.pylons_lang = list(languages)

        self._gnu = None
        for language in languages:
            path = self._find(domain, localedir, language)
            if path is not None:
                with open(path, 'rb') as handle:
                    self._gnu = gettext.GNUTranslations(handle)
                break

        if self._gnu is None:
            raise IOError("no translation catalogs for domain %r in %r"
                          % (domain, localedir))

    @staticmethod
    def _find(domain, localedir, language):
        if not localedir or not language:
            return None
        try:
            return gettext.find(domain, localedir, languages=[language])
        except (OSError, IOError):
            return None

    # -- Babel-style signatures ------------------------------------------
    def gettext(self, value, *args, **kwargs):
        if self._gnu is not None:
            message = self._gnu.gettext(value)
        elif getattr(self, '_fallback', None) is not None:
            message = self._fallback.gettext(value)
        else:
            message = value

        if kwargs:
            message = message % kwargs
        elif args:
            message = message % args
        return message

    def ngettext(self, singular, plural, n, *args, **kwargs):
        if self._gnu is not None:
            message = self._gnu.ngettext(singular, plural, n)
        elif getattr(self, '_fallback', None) is not None:
            message = self._fallback.ngettext(singular, plural, n)
        else:
            message = singular if n == 1 else plural

        if kwargs:
            message = message % kwargs
        elif args:
            message = message % args
        return message

    # Pylons exposed the py2 spellings.
    ugettext = gettext
    ungettext = ngettext


def _get_translator(lang, graceful_fail=False, **kwargs):
    """Return a translator for ``lang``.

    When ``graceful_fail`` is true an unloadable language yields an inert
    translator instead of raising; otherwise it raises ``LanguageError``.
    """
    from ..configuration import get_global_config

    config = get_global_config() or {}

    if not isinstance(lang, (list, tuple)):
        lang = [lang]

    try:
        translator = translation(config.get('pylons.package'), None,
                                 languages=list(lang), **kwargs)
    except IOError as error:
        if graceful_fail:
            translator = NullTranslations()
        else:
            raise LanguageError('IOError: %s' % error)

    translator.pylons_lang = list(lang)
    return translator
