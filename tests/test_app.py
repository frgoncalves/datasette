from datasette.app import Datasette
import os
import pytest
import sqlite3
import tempfile
import time


@pytest.fixture(scope='module')
def app_client():
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, 'test_tables.db')
        conn = sqlite3.connect(filepath)
        conn.executescript(TABLES)
        os.chdir(os.path.dirname(filepath))
        ds = Datasette(
            [filepath],
            page_size=50,
            max_returned_rows=100,
            sql_time_limit_ms=20,
        )
        ds.sqlite_functions.append(
            ('sleep', 1, lambda n: time.sleep(float(n))),
        )
        yield ds.app().test_client


def test_homepage(app_client):
    response = app_client.get('/', gather_request=False)
    assert response.status == 200
    assert 'test_tables' in response.text

    # Now try the JSON
    _, response = app_client.get('/.json')
    assert response.status == 200
    assert response.json.keys() == {'test_tables': 0}.keys()
    d = response.json['test_tables']
    assert d['name'] == 'test_tables'
    assert d['tables_count'] == 6


def test_database_page(app_client):
    response = app_client.get('/test_tables', allow_redirects=False, gather_request=False)
    assert response.status == 302
    response = app_client.get('/test_tables', gather_request=False)
    assert 'test_tables' in response.text
    # Test JSON list of tables
    response = app_client.get('/test_tables.json', gather_request=False)
    data = response.json
    assert 'test_tables' == data['database']
    assert [{
        'columns': ['content'],
        'name': '123_starts_with_digits',
        'table_rows': 0,
    }, {
        'columns': ['pk', 'content'],
        'name': 'Table With Space In Name',
        'table_rows': 0,
    }, {
        'columns': ['pk1', 'pk2', 'content'],
        'name': 'compound_primary_key',
        'table_rows': 0,
    }, {
        'columns': ['content'],
        'name': 'no_primary_key',
        'table_rows': 201,
    }, {
        'columns': ['pk', 'content'],
        'name': 'simple_primary_key',
        'table_rows': 2,
    }, {
        'columns': ['pk', 'content'],
        'name': 'table/with/slashes.csv',
        'table_rows': 1,
    }] == data['tables']


def test_custom_sql(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+content+from+simple_primary_key',
        gather_request=False
    )
    data = response.json
    assert {
        'sql': 'select content from simple_primary_key',
        'params': {}
    } == data['query']
    assert [
        {'content': 'hello'},
        {'content': 'world'}
    ] == data['rows']
    assert ['content'] == data['columns']
    assert 'test_tables' == data['database']
    assert not data['truncated']


def test_sql_time_limit(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+sleep(0.5)',
        gather_request=False
    )
    assert 400 == response.status
    assert 'interrupted' == response.json['error']


def test_custom_sql_time_limit(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+sleep(0.01)',
        gather_request=False
    )
    assert 200 == response.status
    response = app_client.get(
        '/test_tables.jsono?sql=select+sleep(0.01)&_sql_time_limit_ms=5',
        gather_request=False
    )
    assert 400 == response.status
    assert 'interrupted' == response.json['error']


def test_invalid_custom_sql(app_client):
    response = app_client.get(
        '/test_tables?sql=.schema',
        gather_request=False
    )
    assert response.status == 400
    assert 'Statement must begin with SELECT' in response.text
    response = app_client.get(
        '/test_tables.json?sql=.schema',
        gather_request=False
    )
    assert response.status == 400
    assert response.json['ok'] is False
    assert 'Statement must begin with SELECT' == response.json['error']


def test_table_page(app_client):
    response = app_client.get('/test_tables/simple_primary_key', gather_request=False)
    assert response.status == 200
    response = app_client.get('/test_tables/simple_primary_key.jsono', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['query']['sql'] == 'select * from simple_primary_key order by pk limit 51'
    assert data['query']['params'] == {}
    assert data['rows'] == [{
        'pk': '1',
        'content': 'hello',
    }, {
        'pk': '2',
        'content': 'world',
    }]


def test_table_with_slashes_in_name(app_client):
    response = app_client.get('/test_tables/table%2Fwith%2Fslashes.csv', gather_request=False)
    assert response.status == 200
    response = app_client.get('/test_tables/table%2Fwith%2Fslashes.csv.jsono', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['rows'] == [{
        'pk': '3',
        'content': 'hey',
    }]


@pytest.mark.parametrize('path,expected_rows,expected_pages', [
    ('/test_tables/no_primary_key.jsono', 201, 5),
    ('/test_tables/paginated_view.jsono', 201, 5),
    ('/test_tables/123_starts_with_digits.jsono', 0, 1),
])
def test_paginate_tables_and_views(app_client, path, expected_rows, expected_pages):
    fetched = []
    count = 0
    while path:
        response = app_client.get(path, gather_request=False)
        count += 1
        fetched.extend(response.json['rows'])
        path = response.json['next_url']
        if path:
            assert response.json['next'] and path.endswith(response.json['next'])
        assert count < 10, 'Possible infinite loop detected'

    assert expected_rows == len(fetched)
    assert expected_pages == count


def test_max_returned_rows(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+content+from+no_primary_key',
        gather_request=False
    )
    data = response.json
    assert {
        'sql': 'select content from no_primary_key',
        'params': {}
    } == data['query']
    assert data['truncated']
    assert 100 == len(data['rows'])


def test_view(app_client):
    response = app_client.get('/test_tables/simple_view', gather_request=False)
    assert response.status == 200
    response = app_client.get('/test_tables/simple_view.jsono', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['rows'] == [{
        'upper_content': 'HELLO',
        'content': 'hello',
    }, {
        'upper_content': 'WORLD',
        'content': 'world',
    }]


def test_row(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key/1',
        allow_redirects=False,
        gather_request=False
    )
    assert response.status == 302
    assert response.headers['Location'].endswith('/1')
    response = app_client.get('/test_tables/simple_primary_key/1', gather_request=False)
    assert response.status == 200
    response = app_client.get('/test_tables/simple_primary_key/1.jsono', gather_request=False)
    assert response.status == 200
    assert [{'pk': '1', 'content': 'hello'}] == response.json['rows']


TABLES = '''
CREATE TABLE simple_primary_key (
  pk varchar(30) primary key,
  content text
);

CREATE TABLE compound_primary_key (
  pk1 varchar(30),
  pk2 varchar(30),
  content text,
  PRIMARY KEY (pk1, pk2)
);

CREATE TABLE no_primary_key (
  content text
);

CREATE TABLE [123_starts_with_digits] (
  content text
);

CREATE VIEW paginated_view AS
    SELECT
        content,
        '- ' || content || ' -' AS content_extra
    FROM no_primary_key;

CREATE TABLE "Table With Space In Name" (
  pk varchar(30) primary key,
  content text
);

CREATE TABLE "table/with/slashes.csv" (
  pk varchar(30) primary key,
  content text
);

INSERT INTO simple_primary_key VALUES (1, 'hello');
INSERT INTO simple_primary_key VALUES (2, 'world');

INSERT INTO [table/with/slashes.csv] VALUES (3, 'hey');

CREATE VIEW simple_view AS
    SELECT content, upper(content) AS upper_content FROM simple_primary_key;

''' + '\n'.join([
    'INSERT INTO no_primary_key VALUES ({});'.format(i + 1)
    for i in range(201)
])
