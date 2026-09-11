import pytest

from kotwica import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_schema(c)
    yield c
    c.close()
