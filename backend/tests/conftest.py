"""Keep unit tests independent of credentials in the developer's .env."""
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest

# dotenv does not override existing variables, including deliberately empty ones.
for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "DIAG_MODEL"):
    os.environ[name] = ""
_test_data = TemporaryDirectory(prefix="tracelens-tests-")
os.environ["TRACELENS_DATA_DIR"] = _test_data.name


@pytest.fixture
def deepagent_template():
    path = Path(__file__).parents[2] / "frontend/public/templates/deepagent-template.json"
    return json.loads(path.read_text(encoding="utf-8"))
