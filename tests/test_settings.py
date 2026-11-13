from collector.settings import PROJECT_ROOT, resolve_database_url


def test_default_is_project_sqlite_file():
    assert resolve_database_url(None) == f"sqlite:///{(PROJECT_ROOT / 'data' / 'steam_prices.db').as_posix()}"


def test_relative_sqlite_path_is_anchored_to_project_root():
    assert resolve_database_url("sqlite:///data/v2.db") == f"sqlite:///{(PROJECT_ROOT / 'data' / 'v2.db').as_posix()}"


def test_other_urls_are_unchanged(tmp_path):
    absolute = f"sqlite:///{tmp_path.as_posix()}/x.db"
    assert resolve_database_url(absolute) == absolute
    assert resolve_database_url("sqlite://") == "sqlite://"
    pg = "postgresql+psycopg://user:secret@host/db?sslmode=require"
    assert resolve_database_url(pg) == pg


def test_plain_postgres_urls_use_psycopg():
    expected = "postgresql+psycopg://user:secret@host/db?sslmode=require"
    assert resolve_database_url("postgresql://user:secret@host/db?sslmode=require") == expected
    assert resolve_database_url("postgres://user:secret@host/db?sslmode=require") == expected
