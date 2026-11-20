import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import create_app  # noqa: E402
from models import TaskStore  # noqa: E402


@pytest.fixture
def client():
    app = create_app(TaskStore())
    app.config["TESTING"] = True
    return app.test_client()
