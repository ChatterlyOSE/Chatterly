# Porting Chatterly / reddit-r2 to a modern Python — end-to-end assessment

**Date:** 2026-09-22
**Subject:** `ChatterlyOSE/Chatterly` @ `282db6e` ("Self-host the Chatterly/reddit-r2 stack")
**Clone:** `~/src/Chatterly` on `mini.alghul.net` (20 MB; real clone, origin = `https://github.com/ChatterlyOSE/Chatterly.git`)
**Target:** Python **3.14.7** (this Mac's default). Source runtime today: **Python 2.7.6** (Ubuntu 14.04 container `undrfted-app` on frankie).

---

## Verdict up front

The **syntax** port is a solved problem — ~97 % of it is mechanical and I proved it converts.
The **dependencies** are the wall. Specifically **Pylons 1.0.1** (174 files), **pycassa 1.11** (26 files) and
**SQLAlchemy 0.8.4** (16 files) are dead py2-only libraries with no py3 release path; porting them means
replacing the web framework and the Cassandra data-access layer, not fixing syntax.

**Realistic read:** a full port is a multi-person-month project with a framework rewrite in the middle of it.
The tree is, however, unusually well-suited to a **modular/incremental** port, because ~140 of 314 Python
files never import the web framework at all.

---

## PROGRESS — Step 1 COMPLETE (2026-09-22), branch `py3.11-port` @ `4cf4a54`

Mechanical port done and verified in `~/src/Chatterly`, branch **`py3.11-port`** (1 commit off `master`,
103 files changed, 518+/437−). Nothing pushed.

- **Syntax: 63 → 0.** Every file now parses under both **Python 3.11.15** and 3.14.7, and
  `compileall` builds bytecode for all 314 files with zero errors/warnings.
- `futurize --stage1` did the bulk (102 files touched); hand-fixed 4 py2-only `ur"..."` regex literals.
- **The `future` package shim was removed**: futurize had added `from future.utils import raise_` in 8
  files; all 15 `raise_(Cls, value)` sites converted to native `raise Cls(value)`. Net new runtime
  dependency: **none — pure stdlib.**
  *(Correction to the earlier claim in my first pass: I initially reported "zero new dependencies" on a
  grep that required the exact text `from future import` and therefore missed `from future.utils import`.)*
- Two latent bugs fixed, both surfaced only by `compileall` (ast.parse misses them): a misplaced
  `from __future__ import with_statement` in `models/subreddit.py`, and `val is 't'` → `val == 't'` in
  `db/tdb_lite.py` + `db/tdb_sql.py`.

**Still owed (not touched by this step):** py2 idiom semantics (`iteritems` 90 files, `itervalues` 24,
`iterkeys` 8, `basestring` 18, `unicode()` 11, `__metaclass__` 7); py2-only stdlib imports that **fail at
runtime** (`urllib2` 7, `urlparse` 15, `cStringIO` 8, `cPickle` 12, `ConfigParser` 5, `httplib` 6,
`Queue` 3, `Cookie` 2, `HTMLParser` 1); the dead dependencies (Pylons/pycassa/SQLAlchemy 0.8.4/boto2);
the C extensions. The tree now **parses** cleanly but will not **run** until the stdlib imports and the
framework are addressed.

---

## PROGRESS — Step 2 COMPLETE: the runtime layer (2026-09-22), commit `f3021f9`

Everything the previous section listed as "still owed" at the *language* level is now done. 175 files
changed across the two commits (`4cf4a54` syntax, `f3021f9` runtime).

- **Stdlib imports retargeted**, per site rather than blindly: `urlparse`→`urllib.parse`,
  `cStringIO`/`StringIO`→`io` (BytesIO for binary producers, StringIO for text — 12 sites decided
  individually), `urllib2`→`urllib.request` **except** `URLError`/`HTTPError` which live in
  `urllib.error` on py3, `httplib`→`http.client`, `ConfigParser`→`configparser` (`SafeConfigParser`
  removed in 3.12), `Queue`→`queue`, `Cookie`→`http.cookies` (only the `CookieError` import — the r2
  `Cookie` class is unrelated), `HTMLParser`→`html.parser`, `cPickle`→`pickle`, `new.classobj`→`type()`,
  bare `import urllib` + `urllib.quote/urlencode/unquote` → `urllib.parse.*`.
- **Idioms**: `.iteritems/.itervalues/.iterkeys` → `items/values/keys` (316 sites), `xrange`→`range`,
  `basestring`→`str`, `long()`→`int()`, `isinstance(..., long)` dropped.
- **Text**: rewrote `r2/r2/lib/unicode.py` (`_force_unicode`/`_force_utf8`) for py3 — decodes bytes with a
  latin1 fallback and `str()`s everything else; `_force_utf8` no longer wraps `encode()` in `str()` (that
  would yield `"b'...'"`). Converted the 19 `unicode()` call sites; `isinstance(x, unicode)`→`str`;
  `class _Unsafe(unicode)`→`(str)`. No `__unicode__` methods existed.
- **Classes/ordering**: `__metaclass__ = M` → `metaclass=` in the class statement (9 classes); py2
  `__cmp__` (ignored on py3) → `__lt__`/`__eq__`/`__hash__`; `sort(cmp=f)` → `sort(key=cmp_to_key(f))`.
- **Removed in py3**: `inspect.getargspec`→`getfullargspec`, `time.clock`→`perf_counter`,
  `reload()`→`importlib.reload()`, `itertools.izip/ifilter`→`zip/filter`,
  `string.letters/lowercase/uppercase`→`string.ascii_*`, old-style `raise "string"`→`raise ValueError(...)`.

**Verified:** 0/314 files fail a parse on **3.11.15 and 3.14.7**; `compileall` clean; **pyflakes**
py2-builtin undefined-name findings **26 → 19**, the 7 removed being exactly
`basestring/cmp/execfile/long/reload/unicode/xrange`, with **zero introduced**.

**Remaining wall = the dependency layer, unchanged:** Pylons 1.0.1 (174 files), pycassa (26),
SQLAlchemy 0.8.4 (16), boto2, amqplib; C extensions (6 `.pyx`, Cfilters, snudown). Plus semantic risks a
static check cannot see: int/int division, `map`/`filter`/`zip` laziness, dict-view mutation, str-vs-bytes
in paths pyflakes cannot reach. The tree now parses *and* is free of py2-only names, but still imports
Pylons on the web path, so it does not yet run.

---

## PROGRESS — Step 3: the framework layer (2026-09-22), commits `8f9d0eb`, `6245155`

The tree **parses and compiles** on 3.11/3.14, and the two dead frameworks it was written against are
now replaced with vendored py3 equivalents. Strategy: rather than rewrite framework idioms across the
importers, reimplement the *used* surface and vendor it under the original name.

- **`pylons/` (commit `8f9d0eb`)** — 14 files, vendored at `r2/pylons/`. Pylons 1.0.1 is py2-only with no
  py3 release, and it is imported by **179 modules** plus the plugin repos plus Mako's string-template
  import list. The shim covers the measured surface (`app_globals as g` ×160, `tmpl_context as c` ×82,
  `request` ×55, `response` ×15, `config`/`session`/`url_for`, `WSGIController`, `controllers.util.abort`
  /`redirect` ×8, `i18n._`/`N_`/`ungettext`/`get_lang` ×53, `i18n.translation` ×4 names,
  `middleware.ErrorHandler`, `wsgiapp.PylonsApp`, `error.handle_mako_error`, `configuration.PylonsConfig`,
  `util.{PylonsContext,AttribSafeContextObj,ContextObj}`) on WebOb 1.8 + Routes 2.5. Zero call-site churn.
  `StackedObjectProxy` replaces paste.registry with a thread-local stack; `Registry` keeps the
  `environ['paste.registry']` key and `replace(proxy, obj)` because `r2.lib.translation` dereferences it.
  `WSGIController` takes the action from `routes_dict['action']` because `BaseController` has already
  rewritten `listing` → `GET_listing`.
- **Paste 1.7.5 runtime path (commit `6245155`)** — Paste is py2-only; PasteDeploy (its py3 successor)
  ships only `paste.deploy`, and the two cannot be co-installed as a namespace package. So
  `r2/lib/paste_compat.py` provides `Cascade`, `StatusBasedForward`, `RegistryMiddleware`,
  `StaticURLParser` (via `webob.static`), `path_info_split`, `asbool` and the two mimeparse helpers; four
  files now import from it. `StatusBasedForward` reproduces the part that matters: it serves the error
  document and forces the **original** status back on, so a 404 is still a 404 after the page renders.
  `weberror` (Paste's debug page, py2-only) is guarded out; `Reporter` comes from
  `r2/lib/weberror_compat.py`.

**Verified:** whole tree compiles on 3.11; **33/33** checks in `r2/pylons/test_pylons_compat.py` and
**26/26** in `r2/r2/lib/test_paste_compat.py` — including a case pinning that the registry lifetime spans
the middleware chain (`error_mapper` reads `c` *after* the inner app returns, so `PylonsApp` must only pop
a registry it created itself).

## PROGRESS — Step 4: the remaining dependency wall (in flight)

Four parallel workstreams, each with its own file set:

1. **C extensions** — 6 `.pyx` + `Cfilters` (`r2/lib/c/filters.c`). Critical path: `r2.lib.wrapped`, `sgm`,
   `utils._utils`, `db._sorts`, `mr_tools._mr_tools`, `utils.comment_tree_utils`, `Cfilters` are imported
   at module load by models/controllers. Shipping `.so` must be rebuilt on the Linux target (a macOS build
   only proves the ported source compiles).
2. **boto 2.38** — 9 modules (traffic, sitemaps, media/s3, s3_helpers, 2 scripts). Test first whether a
   late boto 2.x import works on py3.11; it may be a version bump. S3/EMR/SQS are not on this deployment's
   live serve path.
3. **amqplib/haigha → pika** — one file, `r2/r2/lib/amqp.py`, but a wide public API (queue declaration,
   `add_item`, `handle_items`) that the 125 consumer jobs depend on.
4. **pycassa → py3 Cassandra client** — 26 modules. **The feasibility wall.** Cassandra 1.2.19 speaks
   Thrift; `cassandra-driver` 3.x speaks only the CQL native protocol v3+ (Cassandra 2.1+). The client
   cannot reach the deployed server however the call sites are rewritten, so the route is a vendored
   py3 `pycassa`-compatible package over Thrift — preserving the column-family data model — NOT a CQL
   rewrite and NOT a Cassandra upgrade.

Not yet started: the `paster` CLI replacement (`r2/commands.py` uses PasteScript/fixture — production
jobs launch as `paster run`), the test harness (`paste.fixture` → WebTest), `r2/setup.py`'s
`install_requires`, and the actual boot against the live stack.

---

## Methodology (all numbers below are measured, not estimated)

| Probe | How |
|---|---|
| Scale | file/LOC walk over the tree |
| Syntax breakage | **empirical** — `ast.parse()` of every file under Python 3.14 (`SyntaxError` = py2-only syntax) |
| Automated conversion | `futurize --stage1` run over a **copy** of the tree, then re-run the 3.14 parse test |
| Semantics owed | residual idiom grep on the converted copy |
| Dependencies | `r2/setup.py` `install_requires` + **actual installed versions** read from the live container via `pkg_resources` |
| Tooling | tested whether `2to3`/`futurize` run on 3.14, 3.11 |

---

## 1. Scale

- **314** Python files, **105,375** LOC
- Closely related but separate: JS/CSS static pipeline (node/less/uglifyjs — not a Python concern)
- Web layer is monolithic: `r2/r2/{controllers,models,lib,templates,config}`

## 2. Layer 1 — Syntax (solved, mechanically)

**59 of 314 files (19 %) do not parse under Python 3.** Failure classes:
`print` statements, `raise X, y`, `except X, e`, octal `0777`, `exec` statement, `ur""` literals.

`futurize --stage1` over a copy of the tree:

```
BEFORE:  59 files fail py3 parse
AFTER:    2 files fail py3 parse      <-- 97% cleared
```

The 2 survivors are both the **py2-only `ur"..."` literal prefix** (`'u' and 'r' prefixes are incompatible`):
- `r2/r2/lib/automoderator.py:406`
- `r2/r2/lib/utils/utils.py:467`

That is a hand-edit (`ur"…"` → `r"…"`). So: **syntax is a scripted job plus two edits.**

## 3. Layer 2 — Semantics the converter does NOT fix

Residual py2 idioms in the converted tree (files affected):

- `dict.iteritems()` **90**, `itervalues()` **24**, `iterkeys()` **8**
- `unicode()` **28** · `basestring` **18** · `__metaclass__` **7**
- py2-only stdlib imports in **42 files** — `urlparse` 15, `cPickle` 12, `cStringIO` 9, `urllib2` 8, `httplib` 6, `ConfigParser` 5, plus `StringIO`/`Queue`/`Cookie`/`HTMLParser`/`new`

These are stage-2 of futurize plus a `six`/`future` shim, or direct rewrites. Also unavoidable at runtime:
**bytes vs text semantics** (r2 is deeply str/unicode-sensitive — cookie signing, media paths, markdown),
integer division, and dict/`None` ordering.

## 4. Layer 3 — THE WALL: dependencies

Installed versions read from the live container; import-site counts from the tree.

**Dead, py2-only, no py3 release — must be replaced:**

| Library | Installed | Import sites | Replacement | Nature of work |
|---|---|---|---|---|
| **Pylons** | **1.0.1** (last release 2012) | **174 files** | Pyramid *(official successor)* or FastAPI/Flask | **Web-framework rewrite.** Controllers, `app_globals`, request/response, routing, template helpers |
| **pycassa** | **1.11.0** | **26 files** | `cassandra-driver` (CQL) | **Data-access rewrite.** Thrift get/set → CQL; access-pattern redesign |
| **boto** | 2.38.0 | 9 | `boto3` | S3/SES API rewrite |
| **amqplib** | 1.1.0 | 1 | `amqp` / `pika` / `kombu` | Small, low risk |
| **pycrypto** | 2.6.1 | 3 (`Crypto`) | `pycryptodome` | Mostly drop-in |
| **python-openid** | 2.2.5 | — | dead — drop or move to OAuth | Feature decision |
| **Beaker** | 1.6.3 | — | framework sessions | Folded into web rewrite |
| **webhelpers** | 1.3 | — | `webhelpers2` | or drop |
| **FormEncode** | 1.2.6 | — | FormEncode ≥2 (py3) | Validation rewrite |
| **nose** | 1.3.4 | — | `pytest` | test runner (dead upstream) |
| **Paste / PasteScript** | 1.7.5.1 / 1.7.5 | 14 | modern WSGI entrypoints | packaging/entrypoints |

**Has py3 but with API churn (work, not a wall):**
SQLAlchemy **0.8.4** → py3 only from 1.4+ (2.0 API differs) · psycopg2 2.4.5 · Pillow 2.3.0 · lxml 3.3.3 ·
Mako 0.9.1 · Routes 2.1.dev0 · WebOb 1.3.1 · gevent 1.0 · gunicorn 17.5 · Babel 1.3 · thrift **0.1** ·
baseplate 0.28.6 (reddit's own framework, py2-era).

**Compiled / C extensions — need py3-clean rebuilds:**
- 6 **Cython** `.pyx` (`_utils`, `comment_tree_utils`, `sgm`, `_sorts`, `_mr_tools`, …)
- `Cfilters` C extension (`r2/lib/c/filters.c`)
- **snudown** — reddit's C markdown parser, pulled from a GitHub tarball at a pinned tag
- `pycaptcha` (from an S3 tarball), `l2cs`, `python-snappy`, `pylibmc`

## 5. Layer 4 — Build & tooling (itself py2)

- `r2/Makefile.py`, `r2/updateini.py`, `r2/setup.py` are **all py2** (bare `print`) — the build and
  config-generation path cannot run on py3 as-is. Verified `Makefile.py` fails 3.14 parse.
- **`2to3` was removed in Python 3.13.** `futurize`/`pasteurize` depend on `lib2to3` and therefore
  **cannot run on 3.14**. They **do** work on ≤3.12 — verified on this Mac's `python3.11`.
  → the converter must run in a **3.11/3.12 toolchain env**, separate from the 3.14 target. `uv` is
  available to provision that interpreter anywhere.
- 125 upstart jobs on the current host → systemd units / container services.

## 6. Layer 5 — Verification strategy (thin)

- **4** test files, **413** test functions, runner = `nose` (dead). CI configs (`drone.yml`, `travis.yml`)
  are stale.
- There is **no usable unit-test safety net** for a rewrite of this size. Verification has to be
  **behavioural against a running stack** (the frankie deployment is that stack: pages, auth, search,
  queues) plus golden-output diffing of templates and API responses.
- **No ready-made py3 port exists** to build on: `ChatterlyOSE/Chatterly` has only `master` + `imgbot`
  branches, and the upstream reddit-archive tree is py2.

---

## TARGET DECISION: Python 3.11 (owner-set 2026-09-22) — damage assessment

**The one real cost — the clock.** 3.11's security EOL is **2027-10-31** (~13 months out);
latest is 3.11.16 and the branch is actively maintained (`python:3.11-slim` was rebuilt 2026-09-19).
Versus 3.12 → 2028-10, 3.13 → 2029-10, 3.14 → 2030-10. If the port runs 3–6 months you land with
roughly 7–10 months of runway. **But the follow-on 3.11→3.12→3.14 hop is a routine py3→py3 upgrade —
orders of magnitude cheaper than the py2→py3 job being done now.** So this is damage *deferred*, not
compounded. Plan the hop; don't fear it.

**Minor costs.** No official binary installers exist for 3.11 past 3.11.9 (later releases are
source-only) — irrelevant for containers/Linux, and this Mac already has a framework 3.11. And 3.11 is
four releases behind, so ecosystem floor-creep will eventually push; not a risk for this dependency set.

**What is NOT damage — 3.11 is the lowest-friction landing spot for this tree:**
- **Zero stdlib-removal debt.** The tree uses *none* of the modules 3.12 removed — `distutils`, `imp`,
  `asyncore`, `asynchat`, `smtpd` → **0 files each**.
- `cgi` (2 files) and `crypt` (1 file) still exist on 3.11 — they'd break on 3.13, so 3.11 *insulates*
  against PEP 594.
- No 3.12+ syntax needed: 0 uses of `match`, 0 walrus operators. `setup.py` builds via setuptools.
- **The converter is native.** `2to3`, `futurize`, `pasteurize` and `lib2to3` all exist in 3.11 → a
  **single interpreter for convert + run + test**, removing the toolchain split described in §5.

**Dependency-side damage: none.** Every modern replacement supports 3.11 (PyPI `requires_python`, checked
2026-09-22): pyramid 2.1 (≥3.10), cassandra-driver 3.30.1 (≥3.10), boto3 1.43 (≥3.10), pycryptodome
3.23, amqp 5.4 (≥3.10), SQLAlchemy 2.0.54 (≥3.7), psycopg2 2.9.13, Pillow 12.3, lxml 6.1, Mako 1.4,
Routes 2.5.1, WebOb 1.8.11, gevent 26.9, gunicorn 26.2, Cython 3.3, pytest 9.1, bcrypt 5.0, Babel 2.18,
beautifulsoup4 4.15, requests 2.34, webhelpers2 2.1, formencode 2.1.1. **No package has a floor above 3.11.**

**Unchanged by this decision:** the wall itself — Pylons→Pyramid, pycassa→cassandra-driver, boto2→boto3,
and the C extensions (6 `.pyx`, `Cfilters`, **snudown**, the one compiled piece needing real py3 work).
Target choice does not reduce that work at all.

**Side note:** 3.10's EOL is **2026-10-31 — next month.** If any component of this stack sits on 3.10,
that is the genuinely urgent one.

---

## Options

**A. Full port to 3.12+** — futurize — hand-fix the 2 files — replace Pylons (Pyramid), pycassa
(cassandra-driver), boto, amqplib, et al. — rebuild C ext — port the build scripts — re-verify end-to-end.
Clean modern end state; the largest single cost is the framework + Cassandra rewrite. **Order of months,
multi-person.**

**B. Incremental / modular (recommended if py3 is the goal)** — the tree supports it: **~140 of 314 files do
not import Pylons.** Carve out py3-able leaf modules and services as separate processes (this is already the
house style — the OpenSearch search provider was added as a clean sidecar), keeping the py2 web core running
in its container meanwhile. Port proceeds underneath a working site; each slice is independently verifiable.

**C. Freeze on 2.7.6 in the container (status quo)** — it *works today* on frankie. Zero port cost, accepts
EOL interpreter risk (no upstream security fixes), keeps the 2014 dependency tree alive.

**Recommendation:** **C + B**. Keep the working py2 core in the container (no outage risk), and start
extracting py3 services module-by-module, highest-value first. Only commit to A if a py3 end state is a
hard requirement, in which case the Pylons→Pyramid and pycassa→cassandra-driver conversions should be
spiked *first*, since they carry the schedule risk.

## First concrete steps, in order

1. Stand up a **Python 3.11/3.12 converter environment** (`uv`), run `futurize --stage1` on a branch.
2. Hand-fix the two `ur""` files; re-run the 3.14 parse test to reach **0 syntax failures**.
3. Spike the two schedule risks in isolation: a hello-world **Pyramid** app wired to r2's config/globals,
   and a **cassandra-driver** read of one existing table. These decide feasibility before any broad work.
4. Inventory the C extensions' py3 readiness (`snudown` is the notable one) and the build scripts.
5. Build the behavioural test harness against the live frankie stack (login, submit, vote, search, queue
   drain) to have something to regress against.
