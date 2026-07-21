import sys
from pathlib import Path

import pytest

# Add parent directory to path to import webapp
sys.path.insert(0, str(Path(__file__).parent.parent))

import webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "INPUT_DIR", str(tmp_path / "input"))
    monkeypatch.setattr(webapp, "OUTPUT_DIR", str(tmp_path / "output"))
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c
