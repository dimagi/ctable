from django.conf import settings
import sqlalchemy
from mock import patch
from datetime import date, datetime, timedelta
from ctable.tests import TestBase
from ctable.backends import SqlBackend
from ctable.base import CtableExtractor, fluff_view
from ctable.models import SqlExtractMapping, ColumnDef, KeyMatcher

DOMAIN = "test"
MAPPING_NAME = "demo_extract"
TABLE = "%s_%s_%s" % (settings.CTABLE_PREFIX, DOMAIN, MAPPING_NAME)


class TestCTable(TestBase):

    def setUp(self):
        self.connection = self.engine.connect()
        self.trans = self.connection.begin()
        self.db.reset()
        self.ctable = CtableExtractor(self.db, SqlBackend(self.connection))

        self.p2 = patch("ctable.base.get_db", return_value=self.db)
        self.p2.start()

    def tearDown(self):
        super(TestCTable, self).tearDown()
        self.trans.rollback()
        self.trans = self.connection.begin()
        self.connection.execute('DROP TABLE IF EXISTS "%s"' % TABLE)
        self.connection.execute('DROP TABLE IF EXISTS "%s_%s"' % (DOMAIN, self._get_fluff_diff()['doc_type']))
        self.trans.commit()
        self.connection.close()
        self.p2.stop()

    def test_basic(self):
        self.db.add_view('c/view', [
            (
                {'reduce': True, 'group': True, 'startkey': [], 'endkey': [{}]},
                [
                    {"key": ["1", "indicator_a", "2013-03-01T12:00:00.000Z"],
                     "value": {"sum": 1, "count": 3, "min": 1, "max": 1, "sumsqr": 3}},
                    {"key": ["1", "indicator_b", "2013-03-01T12:00:00.000Z"],
                     "value": {"sum": 2, "count": 2, "min": 1, "max": 1, "sumsqr": 2}},
                    {"key": ["2", "indicator_a", "2013-03-01T12:00:00.000Z"],
                     "value": {"sum": 3, "count": 3, "min": 1, "max": 1, "sumsqr": 3}},
                ]
            )
        ])

        extract = SqlExtractMapping(domains=[DOMAIN], name=MAPPING_NAME, couch_view="c/view", columns=[
            ColumnDef(name="username", data_type="string", max_length=50, value_source="key", value_index=0),
            ColumnDef(name="date", data_type="date", date_format="%Y-%m-%dT%H:%M:%S.%fZ",
                      value_source="key", value_index=2),
            ColumnDef(name="rename_indicator_a", data_type="integer", value_source="value", value_attribute="sum",
                      match_keys=[KeyMatcher(index=1, value="indicator_a")]),
            ColumnDef(name="indicator_b", data_type="integer", value_source="value", value_attribute="sum",
                      match_keys=[KeyMatcher(index=1, value="indicator_b")]),
            ColumnDef(name="indicator_c", data_type="integer", value_source="value", value_attribute="sum",
                      match_keys=[KeyMatcher(index=1, value="indicator_c")])
        ])

        self.ctable.extract(extract)

        result = dict(
            [(row.username + "_" + self.format_date(row.date), row) for row in
             self.connection.execute('SELECT * FROM %s' % extract.table_name)])
        self.assertEqual(result['1_2013-03-01']['rename_indicator_a'], 1)
        self.assertEqual(result['1_2013-03-01']['indicator_b'], 2)
        self.assertEqual(result['2_2013-03-01']['rename_indicator_a'], 3)
        self.assertIsNone(result['2_2013-03-01']['indicator_b'])

    def test_extra_query_params(self):
        self.db.add_view('c/view', [
            (
                {'reduce': True, 'group': True, 'startkey': [], 'endkey': [{}], 'stale': 'ok'},
                [
                    {"key": ["1", "indicator_b", "2013-03-01T12:00:00.000Z"],
                     "value": {"sum": 2, "count": 2, "min": 1, "max": 1, "sumsqr": 2}},
                ]
            )
        ])

        extract = SqlExtractMapping(domains=[DOMAIN], name=MAPPING_NAME, couch_view="c/view", columns=[
            ColumnDef(name="username", data_type="string", max_length=50, value_source="key", value_index=0),
            ColumnDef(name="date", data_type="date", date_format="%Y-%m-%dT%H:%M:%S.%fZ",
                      value_source="key", value_index=2),
            ColumnDef(name="indicator_b", data_type="integer", value_source="value", value_attribute="sum",
                      match_keys=[KeyMatcher(index=1, value="indicator_b")]),
        ], couch_view_params={'stale': 'ok'})

        self.ctable.extract(extract)

        result = dict(
            [(row.username + "_" + self.format_date(row.date), row) for row in
             self.connection.execute('SELECT * FROM %s' % extract.table_name)])
        self.assertEqual(result['1_2013-03-01']['indicator_b'], 2)

    def test_null_column(self):
        self.db.add_view('c/view', [
            (
                {'reduce': True, 'group': True, 'startkey': [], 'endkey': [{}]},
                [
                    {"key": ["1", "indicator_a", None], "value": 1},
                ]
            )
        ])

        extract = SqlExtractMapping(domains=[DOMAIN], name=MAPPING_NAME, couch_view="c/view", columns=[
            ColumnDef(name="username", data_type="string", max_length=50, value_source="key", value_index=0),
            ColumnDef(name="date", data_type="date", date_format="%Y-%m-%dT%H:%M:%S.%fZ",
                      value_source="key", value_index=2),
            ColumnDef(name="indicator", data_type="integer", value_source="value",
                      match_keys=[KeyMatcher(index=1, value="indicator_a")])
        ])

        self.ctable.extract(extract)

        result = self.connection.execute('SELECT * FROM %s' % extract.table_name).first()
        self.assertEqual(result['username'], "1")
        self.assertEqual(result['date'], date.min)
        self.assertEqual(result['indicator'], 1)

    def test_empty_view_result(self):
        extract = SqlExtractMapping(domains=[DOMAIN], name=MAPPING_NAME, couch_view="c/view", columns=[
            ColumnDef(name="username", data_type="string", max_length=50, value_source="key", value_index=0)
        ])

        self.ctable.extract(extract)

        metadata = sqlalchemy.MetaData(bind=self.engine)
        metadata.reflect()
        self.assertNotIn(extract.table_name, metadata.tables)

    def test_couch_rows_to_sql(self):
        extract = SqlExtractMapping(domains=[DOMAIN], name=MAPPING_NAME, couch_view="c/view", columns=[
            ColumnDef(name="username", data_type="string", max_length=50, value_source="key",
                           value_index=0, null_value_placeholder='123abc'),
            ColumnDef(name="date", data_type="date", date_format="%Y-%m-%dT%H:%M:%S.%fZ",
                           value_source="key", value_index=2),
            ColumnDef(name="indicator1", data_type="integer", value_source="value",
                           match_keys=[KeyMatcher(index=1, value="indicator_a")]),
            ColumnDef(name="indicator2", data_type="integer", value_source="value",
                           match_keys=[KeyMatcher(index=1, value="indicator_b")])
        ])
        rows = [
            dict(key=['user1', 'indicator_a', '2012-02-15T00:00:00.000Z'], value=1),
            dict(key=['user2', 'indicator_a', None], value=2),
            dict(key=['user1', 'indicator_b', '2012-02-15T00:00:00.000Z'], value=3),
            dict(key=[None, 'indicator_b', '2012-02-15T00:00:00.000Z'], value=4),
            dict(key=[None, 'indicator_c', '2012-02-15T00:00:00.000Z'], value=4),  # row doesn't match so not returned
        ]
        sql_rows = list(self.ctable.couch_rows_to_sql_rows(rows, extract))
        self.assertEqual(len(sql_rows), 4)
        self.assertEqual(sql_rows[0], dict(username='user1', date=date(2012, 02, 15), indicator1=1))
        self.assertEqual(sql_rows[1], dict(username='user2', date=date.min, indicator1=2))
        self.assertEqual(sql_rows[2], dict(username='user1', date=date(2012, 02, 15), indicator2=3))
        self.assertEqual(sql_rows[3], dict(username='123abc', date=date(2012, 02, 15), indicator2=4))

    def test_couch_rows_to_sql_match_all(self):
        extract = SqlExtractMapping(domains=[DOMAIN], name=MAPPING_NAME, couch_view="c/view", columns=[
            ColumnDef(name="username", data_type="string", max_length=50, value_source="key",
                           value_index=0, null_value_placeholder='123abc'),
            ColumnDef(name="date", data_type="date", date_format="%Y-%m-%dT%H:%M:%S.%fZ",
                           value_source="key", value_index=1)
        ])
        rows = [
            dict(key=['user1', '2012-02-15T00:00:00.000Z'], value=1),
            dict(key=['user2', None], value=2),
        ]
        sql_rows = list(self.ctable.couch_rows_to_sql_rows(rows, extract))
        self.assertEqual(len(sql_rows), 2)
        self.assertEqual(sql_rows[0], dict(username='user1', date=date(2012, 02, 15)))
        self.assertEqual(sql_rows[1], dict(username='user2', date=date.min))

    def test_convert_indicator_diff_to_grains_date(self):
        diff = self._get_fluff_diff(['all_visits'],
                                    group_values=['mock', '123'],
                                    group_names=['domain', 'owner_id'])

        grains = list(self.ctable.get_fluff_grains(diff))
        self.assertEqual(2, len(grains))
        key_prefix = ['MockIndicators', 'mock', '123', 'visits_week', 'all_visits']
        self.assertEqual(grains[0], key_prefix + ["2012-02-24"])
        self.assertEqual(grains[1], key_prefix + ["2012-02-25"])
        self.assertEqual(grains[1], key_prefix + ["2012-02-25"])

    def test_convert_indicator_diff_to_grains_null(self):
        diff = self._get_fluff_diff(['null_emitter'])

        grains = list(self.ctable.get_fluff_grains(diff))
        self.assertEqual(1, len(grains))
        self.assertEqual(grains[0], ['MockIndicators', '123', 'visits_week', 'null_emitter', None])

    def test_convert_indicator_diff_to_extract_mapping(self):
        diff = self._get_fluff_diff()

        em = self.ctable.get_fluff_extract_mapping(diff, 'SQL')

        self.assertEqual(em.table_name, "{0}_test_MockIndicators".format(settings.CTABLE_PREFIX))
        self.assertEqual(len(em.columns), 4)
        self.assertColumnsEqual(em.columns[0], ColumnDef(name='owner_id',
                                                         data_type='string',
                                                         value_source='key',
                                                         value_index=1))
        self.assertColumnsEqual(em.columns[1], ColumnDef(name='date',
                                                         data_type='date',
                                                         date_format='%Y-%m-%d',
                                                         value_source='key',
                                                         value_index=4))
        self.assertColumnsEqual(em.columns[2], ColumnDef(name='visits_week_all_visits',
                                                         data_type='integer',
                                                         value_source='value',
                                                         value_attribute='count',
                                                         match_keys=[KeyMatcher(index=2, value='visits_week'),
                                                                     KeyMatcher(index=3, value='all_visits')]))
        self.assertColumnsEqual(em.columns[3], ColumnDef(name='visits_week_null_emitter',
                                                         data_type='integer',
                                                         value_source='value',
                                                         value_attribute='count',
                                                         match_keys=[KeyMatcher(index=2, value='visits_week'),
                                                                     KeyMatcher(index=3, value='null_emitter')]))

        self.assertEquals(len(self.db.mock_docs), 1)

    def test_convert_indicator_diff_to_extract_mapping_with_existing(self):
        diff = self._get_fluff_diff()

        existing = SqlExtractMapping(domains=diff['domains'], name=diff['doc_type'], couch_view="c/view",
                                          columns=[ColumnDef(name="owner_id",
                                                                  data_type="string",
                                                                  max_length=50,
                                                                  value_source="key",
                                                                  value_index=0)])
        key = [DOMAIN, 'MockIndicators']
        self.db.add_view('ctable/by_name', [
            (
                {'reduce': False, 'stale': False, 'include_docs': True, 'startkey': key, 'endkey': key + [{}]},
                [
                    {'id': '123', 'key': key, 'value': None, 'doc': existing.to_json()}
                ]
            )
        ])

        em = self.ctable.get_fluff_extract_mapping(diff, 'SQL')

        self.assertEqual(em.table_name, "{0}_test_MockIndicators".format(settings.CTABLE_PREFIX))
        self.assertEqual(len(em.columns), 4)
        self.assertColumnsEqual(em.columns[0], ColumnDef(name='owner_id',
                                                         data_type='string',
                                                         value_source='key',
                                                         value_index=1))
        self.assertColumnsEqual(em.columns[1], ColumnDef(name='date',
                                                         data_type='date',
                                                         date_format='%Y-%m-%d',
                                                         value_source='key',
                                                         value_index=4))
        self.assertColumnsEqual(em.columns[2], ColumnDef(name='visits_week_all_visits',
                                                         data_type='integer',
                                                         value_source='value',
                                                         value_attribute='count',
                                                         match_keys=[KeyMatcher(index=2, value='visits_week'),
                                                                     KeyMatcher(index=3, value='all_visits')]))
        self.assertColumnsEqual(em.columns[3], ColumnDef(name='visits_week_null_emitter',
                                                         data_type='integer',
                                                         value_source='value',
                                                         value_attribute='count',
                                                         match_keys=[KeyMatcher(index=2, value='visits_week'),
                                                                     KeyMatcher(index=3, value='null_emitter')]))

        self.assertEquals(len(self.db.mock_docs), 1)
        self.assertIn('CtableFluffMapping_MockIndicators', self.db.mock_docs)
        columns = self.db.mock_docs['CtableFluffMapping_MockIndicators']['columns']
        self.assertEquals(len(columns), 4)
        self.assertTrue(any(x for x in columns if x['name'] == 'owner_id'))
        self.assertTrue(any(x for x in columns if x['name'] == 'date'))
        self.assertTrue(any(x for x in columns if x['name'] == 'visits_week_null_emitter'))
        self.assertTrue(any(x for x in columns if x['name'] == 'visits_week_all_visits'))

    def test_get_rows_for_grains(self):
        r1 = {"key": ['a', 'b', None], "value": 3}
        r2 = {"key": ['a', 'b', '2013-01-03'], "value": 2}
        self.db.add_view(fluff_view, [
            (
                {'reduce': True, 'group': True, 'startkey': r1['key'], 'endkey': r1['key'] + [{}]},
                [r1]
            ),
            (
                {'reduce': True, 'group': True, 'startkey': r2['key'], 'endkey': r2['key'] + [{}]},
                [r2]
            )
        ])

        grains = [
            ['a', 'b', None],
            ['a', 'b', '2013-01-03'],
        ]
        rows = self.ctable.recalculate_grains(grains, 'fluff')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], r1)
        self.assertEqual(rows[1], r2)

    def test_extract_fluff_diff(self):
        rows = [{"key": ['MockIndicators', '123', 'visits_week', 'null_emitter', None], "value": {'count': 3}},
                {"key": ['MockIndicators', '123', 'visits_week', 'all_visits', '2012-02-24'], "value": {'count': 2}},
                {"key": ['MockIndicators', '123', 'visits_week', 'all_visits', '2012-02-25'], "value": {'count': 7}}]

        self.db.add_view(fluff_view, [({'reduce': True, 'group': True, 'startkey': r['key'], 'endkey': r['key'] + [{}]},
                                       [r]) for r in rows])

        diff = self._get_fluff_diff()
        self.ctable.process_fluff_diff(diff, 'SQL')
        result = dict(
            [('%s_%s' % (row.owner_id, row.date), row) for row in
             self.connection.execute('SELECT * FROM "%s_%s_%s"' % (
                 settings.CTABLE_PREFIX,
                 '_'.join(diff['domains']),
                 diff['doc_type'])
             )])

        self.assertEqual(len(result), 3)
        self.assertEqual(result['123_0001-01-01']['visits_week_null_emitter'], 3)
        self.assertEqual(result['123_2012-02-24']['visits_week_all_visits'], 2)
        self.assertEqual(result['123_2012-02-25']['visits_week_all_visits'], 7)

    def test_get_couch_keys(self):
        mapping = SqlExtractMapping(couch_key_prefix=['a'])
        startkey, endkey = self.ctable.get_couch_keys(mapping)

        self.assertEqual(startkey, ['a'])
        self.assertEqual(endkey, ['a', {}])

    def test_get_couch_keys_with_dates(self):
        format = '%Y-%m-%d'
        range = 10
        mapping = SqlExtractMapping(couch_key_prefix=['a'], couch_date_range=range, couch_date_format=format)
        startkey, endkey = self.ctable.get_couch_keys(mapping)

        end = datetime.utcnow()
        start = end - timedelta(days=range)
        self.assertEqual(startkey, ['a', start.strftime(format)])
        self.assertEqual(endkey, ['a', end.strftime(format), {}])

    def _get_fluff_diff(self, emitters=None, group_values=None, group_names=None, type_map=None):
        emitters = emitters or ['all_visits', 'null_emitter']
        group_values = group_values or ['123']
        group_names = group_names or ['owner_id']
        type_map = type_map or {'owner_id': 'string'}

        diff = dict(domains=[DOMAIN],
                    database='fluff',
                    doc_type='MockIndicators',
                    group_values=group_values,
                    group_names=group_names,
                    group_type_map=type_map)
        indicator_changes = []
        if 'null_emitter' in emitters:
            indicator_changes.append(dict(calculator='visits_week',
                                          emitter='null_emitter',
                                          emitter_type='null',
                                          reduce_type='count',
                                          values=[
                                              dict(date=None, value=1, group_by=None),
                                              dict(date=None, value=1, group_by=None),
                                          ]))

        if 'all_visits' in emitters:
            indicator_changes.append(dict(calculator='visits_week',
                                          emitter='all_visits',
                                          emitter_type='date',
                                          reduce_type='count',
                                          values=[
                                              dict(date=date(2012, 2, 24), value=1, group_by=['abc', '123']),
                                              dict(date=date(2012, 2, 25), value=1, group_by=None)
                                          ]))

        diff['indicator_changes'] = indicator_changes
        diff['all_indicators'] = [dict(calculator='visits_week',
                                          emitter='null_emitter',
                                          emitter_type='null',
                                          reduce_type='count'),
                                  dict(calculator='visits_week',
                                          emitter='all_visits',
                                          emitter_type='date',
                                          reduce_type='count')]
        return diff

    def assertColumnsEqual(self, left, right):
        self.assertEqual(left.name, right.name)
        self.assertEqual(left.data_type, right.data_type)
        self.assertEqual(left.date_format, right.date_format)
        self.assertEqual(left.max_length, right.max_length)
        self.assertEqual(left.value_source, right.value_source)
        self.assertEqual(left.value_index, right.value_index)
        self.assertEqual(left.value_attribute, right.value_attribute)
        left_matches, right_matches = left.match_keys, right.match_keys

        self.assertEqual(len(left_matches), len(right_matches))
        for i in range(len(left_matches)):
            self.assertEqual(left_matches[i].index, right_matches[i].index)
            self.assertEqual(left_matches[i].value, right_matches[i].value)

    def format_date(self, d):
        return "%02d-%02d-%02d" % (d.year,d.month,d.day)
