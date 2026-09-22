import os
import tempfile

import app  # noqa: F401  (charge pyproj avant eccodes, voir app/__init__.py)

# Configuration de test, avant tout import de l'application.
_tmp = tempfile.mkdtemp(prefix="greenard-test-")
os.environ.setdefault("GREENARD_ENV", "test")
os.environ["GREENARD_DATA_DIR"] = _tmp
os.environ["GREENARD_CELERY_EAGER"] = "true"
os.environ["GREENARD_COOKIE_SECURE"] = "false"
os.environ["GREENARD_SECRET_KEY"] = "test-secret-key-not-for-production-000000"
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["GREENARD_DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
