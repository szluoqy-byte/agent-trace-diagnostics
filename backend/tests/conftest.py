"""Keep unit tests independent of credentials in the developer's .env."""
import os
from tempfile import TemporaryDirectory

# dotenv does not override existing variables, including deliberately empty ones.
for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "DIAG_MODEL"):
    os.environ[name] = ""
_test_data = TemporaryDirectory(prefix="tracelens-tests-")
os.environ["TRACELENS_DATA_DIR"] = _test_data.name
