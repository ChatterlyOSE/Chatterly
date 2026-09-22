# undrfted

undrfted is a self-hosted community platform — communities, submissions, comments,
voting, moderation and search — built from **legacy Reddit open-source code**.

## Lineage

This repository descends from Reddit's original open-source release: specifically the
**`r2`** application, the Python 2.7 / Pylons-era code that powered reddit.com before
Reddit Inc. archived the upstream repository. Upstream is frozen at Python 2.7, receives
no updates, and accepts no issues or pull requests.

undrfted continues that codebase rather than replacing it: the application, data model
and templates are the original `r2` design, and what this branch carries is the work to
bring it forward onto a modern, supported Python.

## Python 3.11 port

The application was written for **Python 2.7**. It is being ported to **Python 3.11** —
chosen over 3.12+ deliberately: none of the modules that 3.12 removed (`distutils`,
`imp`, `asyncore`, `asynchat`, `smtpd`) are used, the converter tooling (`lib2to3`,
`2to3`, `futurize`) still exists there, and 3.11 insulates the tree from PEP 594 while
staying close to the original semantics.

**Done**

- The whole tree parses and compiles under Python 3.11 (verified on 3.11.15 and 3.14.7).
- The Python 2 language and stdlib layer is converted: `urllib2`/`urlparse`/`httplib`/
  `ConfigParser`/`Queue`/`Cookie`/`HTMLParser`, `cStringIO`/`StringIO`, `cPickle`,
  `unicode`/`basestring`/`long`, `xrange`, `iteritems`/`itervalues`/`iterkeys`,
  `except E, e`, old-style raises, `__metaclass__`, `cmp` ordering, `execfile`.
- The two dead py2-only frameworks are replaced by **vendored Python 3 equivalents that
  keep the original import names**, so no call sites change (which also covers the plugin
  repositories and the framework names embedded in template import strings):
  - `r2/pylons/` — the Pylons 1.0 surface r2 uses, on WebOb + Routes. Pylons is imported
    by 179 modules; the shim means zero churn across them.
  - `r2/r2/lib/paste_compat.py` — the Paste middleware r2 depends on (cascade, error
    documents, static files, per-request registry).
- Both are covered by behavioural tests (`r2/pylons/test_pylons_compat.py`,
  `r2/r2/lib/test_paste_compat.py`).

**In progress**

- The C extensions (six Cython modules plus `Cfilters`), which load at import time.
- The Cassandra client. r2 uses `pycassa`, which is py2-only and Thrift-based. Note the
  deployed database is Cassandra 1.2, which speaks Thrift: `cassandra-driver` cannot
  reach it, since that driver speaks only the CQL native protocol v3+ (Cassandra 2.1+).
  The port therefore preserves the column-family data model rather than rewriting it to CQL.
- The AMQP client (`amqplib` → `pika`), the AWS uses (`boto`), the `paster`-based CLI, the
  test harness and packaging.

The port is **not yet running end-to-end on Python 3.11**; the items above are the
remaining path.

## Layout

- `r2/r2/` — the application (models, controllers, templates, public assets).
- `r2/pylons/` — the vendored Python 3 Pylons-compatible layer.
- `r2/example.ini` — configuration reference; `r2/development.update` — local overrides.
- `r2/setup.py` — packaging and extension build.

## License

Inherited from the original code: the Common Public Attribution License 1.0, with the
Mozilla Public License 1.1 additions described in the file headers. See the header of any
source file for the full notice.
