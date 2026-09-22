# The contents of this file are subject to the Common Public Attribution
# License Version 1.0. (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://code.reddit.com/LICENSE. The License is based on the Mozilla Public
# License Version 1.1, but Sections 14 and 15 have been added to cover use of
# software over a computer network and provide for limited attribution for the
# Original Developer. In addition, Exhibit A has been modified to be consistent
# with Exhibit B.
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License for
# the specific language governing rights and limitations under the License.
#
# The Original Code is reddit.
#
# The Original Developer is the Initial Developer.  The Initial Developer of
# the Original Code is reddit Inc.
#
# All portions of the code written by reddit are Copyright (c) 2006-2015 reddit
# Inc. All Rights Reserved.
###############################################################################

"""OpenSearch search provider for r2.

This is a sibling of ``solr.py``: it implements the same SearchProvider
interface and reuses ``common.py``'s LinkFields/SubredditFields  unchanged, but
speaks the OpenSearch (Elasticsearch-compatible) JSON API -- ``_search`` for
queries and ``_bulk`` for indexing -- instead of Solr's params+XML API.

Design notes
------------

* Transport is stdlib ``httplib`` on purpose: no client-library dependency
  to install or pin.  The module matches the Python 2.7 runtime of the rest of
  the tree.  Only ``_request`` touches the network.
* Field names and semantics are identical to the Lucene/Solr provider, so the
  same documents can be written to either backend without touching
  ``common.py``.
* Solr's schema defines a multiValued catch-all field ``text`` populated by
  ``copyField``, and sets ``df=text``.  The OpenSearch equivalent is
  ``copy_to: "text"`` on every searchable field plus ``default_field: "text"``
  on the ``query_string`` query.  A bare query such as ``coffee`` therefore
  matches the same set of fields as it does under Solr.
* The analyzer mirrors Solr's ``text_general`` field type: standard tokenizer
  + lowercase + stopwords.  (The shipped schema does *not* use the Porter or
  WordDelimiter filters on these fields -- those live on other, unused field
  types in the stock schema file.)
* ``hot`` and ``top`` do not exist as fields in the shipped Solr schema
  either, so sorting by them is already broken on the Solr provider.  They are
  given faithful equivalents here: ``hot`` becomes a painless script sort over
  ``ups + downs``, and ``top`` sorts on ``ups``.
* Solr's open-ended range syntax (``timestamp:1420000000..``) is not valid
  Lucene, so ``_translate_query`` rewrites it to the equivalent
  ``timestamp:[1420000000 TO *]`` before handing the query to OpenSearch's
  Lucene-based ``query_string`` parser.
"""
from __future__ import print_function

import pickle
from datetime import datetime, timedelta
import functools
import http.client as httplib
import json
import re
import socket
import time

from pylons import tmpl_context as c
from pylons import app_globals as g

from r2.lib import amqp, filters
from r2.lib.configparse import ConfigValue
from r2.lib.db.operators import desc
from r2.lib.db.sorts import epoch_seconds
from r2.lib.providers.search import SearchProvider
from r2.lib.providers.search.common import (
        InvalidQuery,
        LinkFields,
        Results,
        safe_get,
        SearchError,
        SearchHTTPError,
        SubredditFields,
    )
import r2.lib.utils as r2utils
from r2.models import (
        Account,
        All,
        AllMinus,
        DefaultSR,
        DomainSR,
        FakeSubreddit,
        Friends,
        Link,
        MultiReddit,
        NotFound,
        Subreddit,
        Thing,
    )


DEFAULT_FACETS = {"reddit": {"count": 20}}

SORTS_DICT = {'text_relevance': '_score',
              'relevance': '_score'}

DEFAULT_TIMEOUT = 30
BULK_CHUNK_SIZE = 1000
TEXT_CATCHALL = 'text'

# Solr range syntax that is not valid for the Lucene query parser.
_OPEN_ENDED_RANGE = re.compile(r'([A-Za-z_][\w\.]*):(\d+)\.\.(?!\d)')
_OPEN_STARTED_RANGE = re.compile(r'([A-Za-z_][\w\.]*):\.\.(\d+)')

# "hot" sort equivalent -- solr used max(hot/45000.0, 1.0) over a field that
# the shipped schema never defines.  Sum of votes is the closest thing we can
# actually compute from indexed data.
_HOT_SCRIPT = (
    "(doc['ups'].size() == 0 ? 0 : doc['ups'].value)"
    " + (doc['downs'].size() == 0 ? 0 : doc['downs'].value)"
)


# ---------------------------------------------------------------------------
# index mappings -- mirror of solr/schema4.xml
# ---------------------------------------------------------------------------

def _analyzer_settings():
    # Mirrors Solr's text_general: StandardTokenizer, StopFilter, LowerCase.
    return {
        "analysis": {
            "analyzer": {
                "text_general": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "stop"],
                },
            },
        },
    }


def _txt(copy=True, keyword=False):
    field = {"type": "text", "analyzer": "text_general"}
    if copy:
        field["copy_to"] = TEXT_CATCHALL
    if keyword:
        # exact-match / aggregation subfield (Solr's facets on an analyzed
        # text field work on tokens; terms aggregations need a keyword).
        field["fields"] = {"keyword": {"type": "keyword", "ignore_above": 256}}
    return field


def _kw(copy=False):
    field = {"type": "keyword"}
    if copy:
        field["copy_to"] = TEXT_CATCHALL
    return field


def _int():
    return {"type": "integer"}


def _bool():
    return {"type": "boolean"}


def _json_default(value):
    """Fallback encoder for values the json module cannot handle directly."""
    if isinstance(value, datetime):
        return int(time.mktime(value.utctimetuple()))
    return str(value)


LINK_PROPERTIES = {
    "fullname": _kw(),
    "title": _txt(),
    "selftext": _txt(),
    "url": _txt(keyword=True),
    "author": _txt(keyword=True),
    "author_fullname": _kw(copy=True),
    "reddit": _txt(keyword=True),
    "subreddit": _txt(keyword=True),
    "name": _txt(keyword=True),
    "type": _txt(keyword=True),
    "site": _kw(copy=True),
    "flair": _txt(),
    "flair_text": _txt(),
    "flair_css_class": _txt(),
    "ups": _int(),
    "downs": _int(),
    "num_comments": _int(),
    "sr_id": _int(),
    "timestamp": _int(),
    "type_id": _int(),
    "over18": _bool(),
    "nsfw": _bool(),
    "self": _bool(),
    "is_self": _bool(),
    TEXT_CATCHALL: {"type": "text", "analyzer": "text_general"},
}

SUBREDDIT_PROPERTIES = {
    "name": _txt(keyword=True),
    "title": _txt(),
    "type": _txt(keyword=True),
    "language": _txt(),
    "header_title": _txt(),
    "description": _txt(),
    "sidebar": _txt(),
    "link_type": _txt(),
    "over18": _bool(),
    "activity": _int(),
    "subscribers": _int(),
    "type_id": _int(),
    TEXT_CATCHALL: {"type": "text", "analyzer": "text_general"},
}


def _index_body(properties):
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": _analyzer_settings()["analysis"],
        },
        "mappings": {"properties": properties},
    }


def links_index():
    return getattr(g, 'opensearch_links_index', 'links')


def subreddits_index():
    return getattr(g, 'opensearch_subreddits_index', 'subreddits')


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

def _request(method, path, body=None, host=None, port=None, timeout=None,
             record_stats=False, content_type='application/json',
             raw_body=None):
    """Perform one OpenSearch HTTP request and return the parsed JSON body.

    Raises InvalidQuery for a 4xx/5xx that carries an OpenSearch error
    document, SearchHTTPError for any other non-2xx, and SearchError if the
    socket itself fails.
    """
    host = host or getattr(g, 'opensearch_host', '127.0.0.1')
    port = port or getattr(g, 'opensearch_port', 9200)
    if timeout is None:
        timeout = getattr(g, 'opensearch_timeout', DEFAULT_TIMEOUT)

    data = raw_body
    if data is None and body is not None:
        data = json.dumps(body)
    headers = {'Content-Type': content_type}

    timer = None
    if record_stats:
        timer = g.stats.get_timer("opensearch_timer")
        timer.start()

    connection = httplib.HTTPConnection(host, port, timeout=timeout)
    response = ''
    try:
        connection.request(method, path, data, headers)
        resp = connection.getresponse()
        response = resp.read()
        if record_stats:
            g.stats.action_count("event.search_query", resp.status)
        if resp.status >= 300:
            response_json = None
            try:
                response_json = json.loads(response)
            except ValueError:
                pass
            if isinstance(response_json, dict) and 'error' in response_json:
                error = response_json['error']
                if isinstance(error, dict):
                    message = (error.get('reason') or error.get('type') or
                               'Unknown error')
                else:
                    message = str(error)
                raise InvalidQuery(resp.status, resp.reason, message,
                                   host, path, response)
            raise SearchHTTPError(resp.status, resp.reason,
                                  host, path, response)
    except socket.error as e:
        raise SearchError(e, host, path)
    finally:
        connection.close()
        if timer is not None:
            timer.stop()

    if not response:
        return {}
    return json.loads(response)


# ---------------------------------------------------------------------------
# query translation: solr/lucene -> opensearch
# ---------------------------------------------------------------------------

def _translate_query(query):
    """Rewrite Solr-only range syntax into Lucene range syntax.

    Everything else is shared: r2 emits Lucene-style field queries
    (``title:coffee AND sr_id:2``) which OpenSearch's query_string parser
    understands directly.
    """
    if not query:
        return query
    query = _OPEN_ENDED_RANGE.sub(r'\1:[\2 TO *]', query)
    query = _OPEN_STARTED_RANGE.sub(r'\1:[* TO \2]', query)
    return query


def _facet_field(name):
    """Aggregations cannot run on an analyzed text field."""
    return '%s.keyword' % name


def _translate_sort(rank):
    """Convert a solr-style sort string into an OpenSearch sort clause."""
    if not rank:
        return None
    rank = rank.strip().lower()
    parts = rank.split()
    field = parts[0]
    direction = parts[1] if len(parts) > 1 else 'desc'
    if direction not in ('asc', 'desc'):
        direction = 'desc'

    if field in ('score', '_score'):
        return [{'_score': {'order': direction}}]
    if field in ('hot', '_hot') or field.startswith('max('):
        return [{'_script': {'type': 'number', 'order': direction,
                             'script': {'lang': 'painless',
                                        'source': _HOT_SCRIPT}}}]
    if field == 'top':
        return [{'ups': {'order': direction}}]
    return [{field: {'order': direction}}]


def _build_search_body(query, bq, faceting, size, start, rank):
    body = {
        "from": start,
        "size": size,
        "track_total_hits": True,
        "query": {
            "query_string": {
                "query": _translate_query(query),
                "default_field": TEXT_CATCHALL,
                # solr's standard parser defaults to OR
                "default_operator": "OR",
                "analyze_wildcard": True,
            },
        },
    }

    sort = _translate_sort(rank)
    if sort:
        body['sort'] = sort

    if faceting:
        aggs = {}
        for field, options in faceting.items():
            terms = {
                "field": _facet_field(field),
                "size": options.get("count", 20),
            }
            order = options.get("sort")
            if order:
                direction = order.strip().split()[-1].lower()
                if direction not in ('asc', 'desc'):
                    direction = 'desc'
                if order.strip().lower().startswith(('count', 'score')):
                    terms["order"] = {"_count": direction}
                else:
                    terms["order"] = {"_key": direction}
            aggs[field] = {"terms": terms}
        body['aggs'] = aggs

    return body


def basic_query(query=None, bq=None, faceting=None, size=1000, start=0,
                rank="", return_fields=None, record_stats=False,
                search_api=None, index=None):
    if search_api is None:
        search_api = getattr(g, 'opensearch_host', '127.0.0.1')
    if index is None:
        index = links_index()
    if faceting is None:
        faceting = DEFAULT_FACETS

    body = _build_search_body(query, bq, faceting, size, start, rank)
    path = '/%s/_search' % index
    return _request('POST', path, body, host=search_api,
                    port=getattr(g, 'opensearch_port', 9200),
                    record_stats=record_stats)


basic_link = functools.partial(basic_query, size=10, start=0, rank="",
                               return_fields=['title', 'reddit',
                                              'author_fullname'],
                               record_stats=False)

basic_subreddit = functools.partial(basic_query, faceting=None, size=10,
                                    start=0, rank="activity",
                                    return_fields=['title', 'reddit',
                                                   'author_fullname'],
                                    record_stats=False,
                                    index=None)


# ---------------------------------------------------------------------------
# query classes
# ---------------------------------------------------------------------------

class OpenSearchSearchQuery(object):
    '''Represents a search query sent to OpenSearch'''
    search_api = None
    # Which index this query class reads.  solr.py could infer this from the
    # host (separate host per core); with a single cluster serving two indices
    # the class must say so explicitly -- otherwise the subreddit query sorts
    # on "activity" against the links index and OpenSearch 400s with
    # "No mapping found for [activity] in order to sort on".
    index_name = 'links'
    recents = {None: None}
    default_syntax = "lucene"

    def __init__(self, query, sr=None, sort=None, syntax=None, raw_sort=None,
                 faceting=None, recent=None, include_over18=True,
                 rank_expressions=None, start=0, num=1000):
        if syntax is None:
            syntax = self.default_syntax
        elif syntax not in self.known_syntaxes:
            raise ValueError("Unknown search syntax: %s" % syntax)

        self.bq = None
        self.query = filters._force_unicode(query or u'')
        self.converted_data = None
        self.syntax = syntax

        # filters
        self.sr = sr
        self._recent = recent
        self.recent = self.recents[recent]
        self.include_over18 = include_over18

        # rank / rank expressions
        self._sort = sort
        if raw_sort:
            self.sort = _translate_raw_sort(raw_sort)
        elif sort:
            self.sort = self.sorts.get(sort)
        else:
            self.sort = '_score'
        self.rank_expressions = rank_expressions

        # pagination
        self.start = start
        self.num = num

        # facets
        self.faceting = faceting

        self.results = None

    def run(self, after=None, reverse=False, num=1000, _update=False):
        self.bq = u''
        results = self._run(_update=_update)

        docs, hits, facets = results.docs, results.hits, results._facets

        after_docs = r2utils.get_after(docs, after, num, reverse=reverse)

        self.results = Results(after_docs, hits, facets)
        return self.results

    def _run(self, start=0, num=1000, _update=False):
        '''Run the search against self.query'''
        if g.sqlprinting:
            g.log.info("%s", self)
        q = self.customize_query(self.query)
        return self._run_cached(q, self.bq, self.sort, self.faceting,
                                start=start, num=num, _update=_update)

    @classmethod
    def get_index(cls):
        if cls.index_name == 'subreddits':
            return subreddits_index()
        return links_index()

    def customize_query(self, q):
        return q

    def __repr__(self):
        '''Return a string representation of this query'''
        result = ["<", self.__class__.__name__, "> query:",
                  repr(self.query), " "]
        if self.bq:
            result.append(" bq:")
            result.append(repr(self.bq))
            result.append(" ")
        result.append("sort:")
        result.append(self.sort)
        return ''.join(result)

    @classmethod
    def _run_cached(cls, query, bq, sort="score", faceting=None, start=0,
                    num=1000, _update=False):
        '''Query the OpenSearch cluster.

        The response shape differs from solr's:

        {
            "hits": {"total": {"value": 1, "relation": "eq"},
                     "hits": [{"_id": "t5_3", "_source": {...}}]},
            "aggregations": {"reddit": {"buckets": [
                {"key": "coffee", "doc_count": 3}]}}
        }
        '''
        if not query:
            return Results([], 0, {})

        try:
            response = basic_query(query=query, bq=bq, size=num, start=start,
                                   rank=sort, search_api=cls.search_api,
                                   faceting=faceting,
                                   index=cls.get_index(),
                                   record_stats=True)
        except (SearchHTTPError, SearchError) as e:
            g.log.error("Search Error: %r", e)
            raise

        total = response['hits']['total']
        if isinstance(total, dict):
            total = total.get('value', 0)
        hits = total

        docs = [hit['_id'] for hit in response['hits'].get('hits', [])]

        facets = {}
        for field, agg in (response.get('aggregations') or {}).items():
            facets[field] = [dict(value=bucket['key'],
                                  count=bucket['doc_count'])
                             for bucket in agg.get('buckets', [])]

        return Results(docs, hits, facets)


class OpenSearchLinkSearchQuery(OpenSearchSearchQuery):
    search_api = None
    index_name = 'links'
    sorts = {
        'relevance': '_score desc',
        'hot': 'hot desc',
        'top': 'top desc',
        'new': 'timestamp desc',
        'comments': 'num_comments desc',
    }
    recents = {
        'hour': timedelta(hours=1),
        'day': timedelta(days=1),
        'week': timedelta(days=7),
        'month': timedelta(days=31),
        'year': timedelta(days=366),
        'all': None,
        None: None,
    }
    known_syntaxes = g.search_syntaxes
    default_syntax = 'lucene'

    def customize_query(self, bq):
        queries = [bq]
        subreddit_query = self._get_sr_restriction(self.sr)
        if subreddit_query:
            queries.append(subreddit_query)
        if self.recent:
            recent_query = self._restrict_recent(self.recent)
            queries.append(recent_query)
        return self.create_boolean_query(queries)

    @classmethod
    def create_boolean_query(cls, queries):
        '''Return an AND clause combining all queries'''
        if len(queries) > 1:
            bq = ' AND  '.join(['(%s)' % q for q in queries])
        else:
            bq = queries[0]
        return bq

    @staticmethod
    def _restrict_recent(recent):
        now = datetime.now(g.tz)
        since = epoch_seconds(now - recent)
        # Lucene range syntax; solr's "since.." is translated in
        # _translate_query for safety, but we emit the portable form here.
        return 'timestamp:[%i TO *]' % since

    @staticmethod
    def _get_sr_restriction(sr):
        '''Return a lucene-appropriate query string that restricts
        results to only contain results from sr

        '''
        bq = []
        if (not sr) or sr == All or isinstance(sr, DefaultSR):
            return None
        elif isinstance(sr, MultiReddit):
            for sr_id in sr.sr_ids:
                bq.append("sr_id:%s" % sr_id)
        elif isinstance(sr, DomainSR):
            bq = ["site:'%s'" % sr.domain]
        elif sr == Friends:
            if not c.user_is_loggedin or not c.user.friends:
                return None
            friend_ids = c.user.friends
            friends = ["author_fullname:'%s'" %
                       Account._fullname_from_id36(r2utils.to36(id_))
                       for id_ in friend_ids]
            bq.extend(friends)
        elif isinstance(sr, AllMinus):
            for sr_id in sr.exclude_sr_ids:
                bq.append("-sr_id:%s" % sr_id)
        elif not isinstance(sr, FakeSubreddit):
            bq = ["sr_id:%s" % sr._id]
        return ' OR '.join(bq)


class OpenSearchSubredditSearchQuery(OpenSearchSearchQuery):
    search_api = None
    index_name = 'subreddits'
    sorts = {
        'relevance': 'activity desc',
    }
    known_syntaxes = ("lucene", "plain")
    default_syntax = "lucene"


def _translate_raw_sort(sort):
    '''translate from cloudsearch syntax'''
    sort_dir = ''
    if sort.startswith('-'):
        sort = sort[1:]
        sort_dir = ' desc'
    sort = SORTS_DICT.get(sort, sort)
    return '%s%s' % (sort, sort_dir)


# ---------------------------------------------------------------------------
# uploaders
# ---------------------------------------------------------------------------

class OpenSearchSearchUploader(object):
    index = None
    types = ()
    use_safe_get = False

    def __init__(self, host=None, port=None, fullnames=None, index=None):
        self.host = host or getattr(g, 'opensearch_host', '127.0.0.1')
        self.port = port or getattr(g, 'opensearch_port', 9200)
        self.index = (index or self.index or links_index())
        self.fullnames = fullnames
        self.things = []

    @classmethod
    def desired_fullnames(cls, items):
        '''Pull fullnames that represent instances of 'types' out of items'''

        fullnames = set()
        type_ids = [type_._type_id for type_ in cls.types]
        for item in items:
            item_type = r2utils.decompose_fullname(item['fullname'])[1]
            if item_type in type_ids:
                fullnames.add(item['fullname'])

        return fullnames

    def batch_lookups(self):
        try:
            self.things = Thing._by_fullname(self.fullnames, data=True,
                                             return_dict=False)
        except NotFound:
            if self.use_safe_get:
                self.things = safe_get(Thing._by_fullname, self.fullnames,
                                       data=True, return_dict=False)
            else:
                raise

    def _coerce(self, doc):
        """Coerce values to the type declared in the index mapping.

        r2's field layer returns 0/1 for the yesno fields, and OpenSearch's
        boolean type only accepts true/false, so the mapping is the source of
        truth for the conversion.

        """
        properties = getattr(self, 'properties', {})
        for name, value in doc.items():
            mapped = properties.get(name)
            if mapped and mapped.get('type') == 'boolean':
                doc[name] = bool(value)
        return doc

    def bulk_lines(self):
        '''Generate the newline-delimited bulk request body along with the
        number of adds and deletes it contains.

        '''
        self.batch_lookups()
        lines = []
        adds, deletes = 0, 0

        for thing in self.things:
            try:
                if thing._spam or thing._deleted:
                    lines.append(json.dumps({"delete": {
                        "_index": self.index,
                        "_id": thing._fullname,
                    }}))
                    deletes += 1
                elif self.should_index(thing):
                    doc = self._coerce(self.fields(thing))
                    doc['fullname'] = thing._fullname
                    lines.append(json.dumps({"index": {
                        "_index": self.index,
                        "_id": thing._fullname,
                    }}))
                    lines.append(json.dumps(doc, default=_json_default))
                    adds += 1
            except (AttributeError, KeyError) as e:
                # Problem! Bail out, which means these items won't get
                # "consumed" from the queue. If the problem is from DB
                # lag or a transient issue, then the queue consumer
                # will succeed eventually. If it's something else,
                # then manually run a consumer with 'use_safe_get'
                # on to get past the bad Thing in the queue
                if not self.use_safe_get:
                    raise
                else:
                    g.log.warning("Ignoring problem on thing %r.\n\n%r",
                                  thing, e)

        return lines, adds, deletes

    def send_bulk(self, lines):
        '''Send a bulk request, chunked to keep individual requests small'''
        responses = []
        for offset in range(0, len(lines), BULK_CHUNK_SIZE * 2):
            chunk = lines[offset:offset + BULK_CHUNK_SIZE * 2]
            data = ('\n'.join(chunk) + '\n').encode('utf-8')
            response = _request('POST', '/_bulk', host=self.host,
                                port=self.port, raw_body=data,
                                content_type='application/x-ndjson')
            responses.append(response)

            if response.get('errors'):
                failures = []
                for item in response.get('items', []):
                    for op, result in item.items():
                        if result.get('error'):
                            failures.append('%s %s: %r' % (
                                op, result.get('_id'), result['error']))
                if failures:
                    raise SearchHTTPError(
                        'bulk', 'partial failure', self.host, '/_bulk',
                        '; '.join(failures[:5]))
        return responses

    def refresh(self):
        try:
            _request('POST', '/%s/_refresh' % self.index,
                     host=self.host, port=self.port)
        except (SearchHTTPError, SearchError) as e:
            # not fatal: documents are durable either way, they just won't be
            # searchable until the next refresh interval.
            g.log.warning("OpenSearch refresh failed: %r", e)

    def delete_ids(self, ids):
        '''Delete documents from the index.
        'ids' should be a list of fullnames

        '''
        lines = [json.dumps({"delete": {"_index": self.index, "_id": id_}})
                 for id_ in ids]
        if not lines:
            return
        self.send_bulk(lines)
        self.refresh()

    def inject(self, quiet=False):
        '''Send things to OpenSearch. Return value is time elapsed, in
        seconds, of the communication with the OpenSearch endpoint

        '''
        lines, adds, deletes = self.bulk_lines()

        cs_time = 0
        if lines:
            cs_start = datetime.now(g.tz)
            self.send_bulk(lines)
            self.refresh()
            cs_time = (datetime.now(g.tz) - cs_start).total_seconds()

        g.stats.simple_event("opensearch.uploads.adds", delta=adds)
        g.stats.simple_event("opensearch.uploads.deletes", delta=deletes)

        if not quiet:
            print("%s Changes: +%i -%i" % (self.__class__.__name__,
                                           adds, deletes))

        return cs_time


class OpenSearchLinkUploader(OpenSearchSearchUploader):
    types = (Link,)
    properties = LINK_PROPERTIES

    def __init__(self, host=None, port=None, fullnames=None, index=None):
        super(OpenSearchLinkUploader, self).__init__(
            host=host, port=port, fullnames=fullnames,
            index=index or links_index())
        self.accounts = {}
        self.srs = {}

    def fields(self, thing):
        '''Return fields relevant to a Link search index'''
        account = self.accounts[thing.author_id]
        sr = self.srs[thing.sr_id]
        return LinkFields(thing, account, sr).fields()

    def batch_lookups(self):
        super(OpenSearchLinkUploader, self).batch_lookups()
        author_ids = [thing.author_id for thing in self.things
                      if hasattr(thing, 'author_id')]
        try:
            self.accounts = Account._byID(author_ids, data=True,
                                          return_dict=True)
        except NotFound:
            if self.use_safe_get:
                self.accounts = safe_get(Account._byID, author_ids, data=True,
                                         return_dict=True)
            else:
                raise

        sr_ids = [thing.sr_id for thing in self.things
                  if hasattr(thing, 'sr_id')]
        try:
            self.srs = Subreddit._byID(sr_ids, data=True, return_dict=True)
        except NotFound:
            if self.use_safe_get:
                self.srs = safe_get(Subreddit._byID, sr_ids, data=True,
                                    return_dict=True)
            else:
                raise

    def should_index(self, thing):
        return (thing.promoted is None and
                getattr(thing, "sr_id", None) != -1)


class OpenSearchSubredditUploader(OpenSearchSearchUploader):
    types = (Subreddit,)
    properties = SUBREDDIT_PROPERTIES

    def __init__(self, host=None, port=None, fullnames=None, index=None):
        super(OpenSearchSubredditUploader, self).__init__(
            host=host, port=port, fullnames=fullnames,
            index=index or subreddits_index())

    def fields(self, thing):
        return SubredditFields(thing).fields()

    def should_index(self, thing):
        return thing._id != Subreddit.get_promote_srid()


@g.stats.amqp_processor('opensearch_q')
def _run_changed(msgs, chan):
    '''Consume the cloudsearch_changes queue, and print reporting information
    on how long it took and how many remain

    '''
    start = datetime.now(g.tz)

    changed = [pickle.loads(msg.body) for msg in msgs]

    link_fns = OpenSearchLinkUploader.desired_fullnames(changed)
    sr_fns = OpenSearchSubredditUploader.desired_fullnames(changed)

    link_uploader = OpenSearchLinkUploader(fullnames=link_fns)
    subreddit_uploader = OpenSearchSubredditUploader(fullnames=sr_fns)

    link_time = link_uploader.inject()
    subreddit_time = subreddit_uploader.inject()
    opensearch_time = link_time + subreddit_time

    totaltime = (datetime.now(g.tz) - start).total_seconds()

    print ("%s: %d messages in %.2fs seconds (%.2fs secs waiting on "
           "opensearch); %d duplicates, %s remaining)" %
           (start, len(changed), totaltime, opensearch_time,
            len(changed) - len(link_fns | sr_fns),
            msgs[-1].delivery_info.get('message_count', 'unknown')))


# ---------------------------------------------------------------------------
# index management
# ---------------------------------------------------------------------------

def ensure_index(index, body, host=None, port=None):
    '''Create an index with an explicit mapping if it does not exist.

    OpenSearch infers mapping types from the first document it sees, which
    mis-maps numeric/boolean fields and then fails on later documents.  r2
    always knows its field types, so it declares them up front.

    '''
    host = host or getattr(g, 'opensearch_host', '127.0.0.1')
    port = port or getattr(g, 'opensearch_port', 9200)
    try:
        _request('PUT', '/%s' % index, body, host=host, port=port)
    except SearchHTTPError as e:
        message = repr(e.args)
        if 'resource_already_exists' in message or 'already exists' in message:
            return False
        raise
    except InvalidQuery as e:
        if 'resource_already_exists' in repr(e.args):
            return False
        raise
    return True


def ensure_indices(host=None, port=None, quiet=False):
    '''Make sure both indices exist with their declared mappings'''
    created = []
    if ensure_index(links_index(), _index_body(LINK_PROPERTIES),
                    host=host, port=port):
        created.append(links_index())
    if ensure_index(subreddits_index(), _index_body(SUBREDDIT_PROPERTIES),
                    host=host, port=port):
        created.append(subreddits_index())
    if not quiet:
        if created:
            print("created indices: %s" % ", ".join(created))
        else:
            print("indices already exist")
    return created


def index_status(host=None, port=None):
    '''Return per-index document counts from the cluster'''
    host = host or getattr(g, 'opensearch_host', '127.0.0.1')
    port = port or getattr(g, 'opensearch_port', 9200)
    counts = {}
    for index in (links_index(), subreddits_index()):
        try:
            response = _request('GET', '/%s/_count' % index,
                                host=host, port=port)
        except (SearchHTTPError, InvalidQuery, SearchError):
            counts[index] = None
        else:
            counts[index] = response.get('count')
    return counts


def _progress_key(item):
    return "%s/%s" % (item._id, item._date)


def _rebuild_link_index(start_at=None, sleeptime=1, cls=Link,
                        uploader=OpenSearchLinkUploader,
                        estimate=50000000, chunk_size=1000):
    uploader = uploader()

    q = cls._query(cls.c._deleted == (True, False), sort=desc('_date'))

    if start_at:
        after = cls._by_fullname(start_at)
        assert isinstance(after, cls)
        q._after(after)

    q = r2utils.fetch_things2(q, chunk_size=chunk_size)
    q = r2utils.progress(q, verbosity=1000, estimate=estimate, persec=True,
                         key=_progress_key)
    for chunk in r2utils.in_chunks(q, size=chunk_size):
        uploader.things = chunk
        uploader.fullnames = [x._fullname for x in chunk]
        for x in range(5):
            try:
                uploader.inject()
            except httplib.HTTPException as err:
                print("Got %s, sleeping %s secs" % (err, x))
                time.sleep(x)
                continue
            else:
                break
        else:
            raise err
        last_update = chunk[-1]
        print("last updated %s" % last_update._fullname)
        time.sleep(sleeptime)


rebuild_subreddit_index = functools.partial(_rebuild_link_index,
                                            cls=Subreddit,
                                            uploader=OpenSearchSubredditUploader,
                                            estimate=200000,
                                            chunk_size=1000)


class OpenSearchSearchProvider(SearchProvider):
    '''Provider implementation: wrap it all up as a SearchProvider

    example config:
    # hostname or IP of the opensearch cluster
    opensearch_host = 127.0.0.1
    # port the cluster's HTTP API is listening on
    opensearch_port = 9200
    # index holding link documents
    opensearch_links_index = links
    # index holding subreddit documents
    opensearch_subreddits_index = subreddits
    # per-request timeout in seconds
    opensearch_timeout = 30
    # default batch size
    # limit is hard-coded to 1000
    # set to 1 for testing
    opensearch_min_batch = 500
    '''

    config = {
        ConfigValue.int: [
            "opensearch_port",
            "opensearch_min_batch",
            "opensearch_timeout",
        ],
        ConfigValue.str: [
            "opensearch_host",
            "opensearch_links_index",
            "opensearch_subreddits_index",
        ],
    }

    InvalidQuery = (InvalidQuery,)
    SearchException = (SearchHTTPError, SearchError)

    SearchQuery = OpenSearchLinkSearchQuery

    SubredditSearchQuery = OpenSearchSubredditSearchQuery

    def run_changed(self, drain=False, min_size=int(getattr(g, 'opensearch_min_batch', 500)),
                    limit=1000, sleep_time=10, use_safe_get=False,
                    verbose=False):
        '''Run by `cron` (through `paster run`) on a schedule to send Things
        to OpenSearch

        '''
        if use_safe_get:
            OpenSearchSearchUploader.use_safe_get = True
        try:
            ensure_indices(quiet=True)
        except (SearchHTTPError, InvalidQuery, SearchError) as e:
            g.log.warning("OpenSearch index bootstrap failed: %r", e)
        amqp.handle_items('cloudsearch_changes', _run_changed, min_size=min_size,
                          limit=limit, drain=drain, sleep_time=sleep_time,
                          verbose=verbose)

    def rebuild_link_index(self, start_at=None, sleeptime=1, cls=Link,
                           uploader=OpenSearchLinkUploader,
                           estimate=50000000, chunk_size=1000):
        _rebuild_link_index(start_at, sleeptime, cls, uploader, estimate,
                            chunk_size)