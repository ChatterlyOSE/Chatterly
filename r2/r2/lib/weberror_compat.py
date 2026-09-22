"""Python 3 stand-in for ``weberror.reporter.Reporter``.

r2's ``r2.lib.log`` subclasses it::

    from weberror.reporter import Reporter

    class RavenErrorReporter(Reporter):
        ...

    class LoggingErrorReporter(Reporter):
        ...

weberror is Python-2-only.  r2 only needs the class to exist and to offer the
``capture_exception`` entry point its own subclasses define/override -- the
subclasses supply the actual raven calls.  This base therefore provides the
minimal contract plus a permissive fallback so overriding hooks that call
back into the base still work.
"""

__all__ = ['Reporter']


class Reporter(object):
    """Minimal base for r2's error reporters."""

    @classmethod
    def capture_exception(cls, exc_info=None):
        """Report an exception.

        r2's RavenErrorReporter overrides this with the real raven client; the
        base keeps the call cheap and safe if something reaches it directly
        (which happens when REDDIT_ERRORS_TO_SENTRY is unset).
        """
        try:
            import raven
        except ImportError:
            return None

        try:
            client = raven.Client()
            return client.captureException(exc_info=exc_info)
        except Exception:
            return None

    def report(self, exc_data):
        """Entry point ErrorMiddleware calls with an exception report."""
        self.capture_exception()

    def format_text(self, exc_data):
        """Render an exception report as text.

        weberror's Reporter returned ``(text, extra_context)``;
        ``LoggingErrorReporter`` (r2/lib/log.py:257) unpacks exactly that.
        """
        formatted = getattr(exc_data, 'exception_formatted', None)
        if formatted:
            return ''.join(formatted), {}
        return str(exc_data), {}
