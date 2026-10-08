"""The setup scripts must run on PCs where Windows Application Control blocks compiled database
libraries (e.g. SQLAlchemy's DLLs). They only talk to Zoho and write files."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = ["sqlalchemy", "psycopg", "alembic", "celery", "redis", "boto3"]

PROBE = f"""
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {BLOCKED!r}:
            raise ImportError('blocked by policy: ' + name)
        return None
sys.meta_path.insert(0, Block())
import app.tools.zoho_auth, app.tools.fetch_account
import app.tools.check_zoho_setup, app.tools.create_zoho_review_module
import app.tools.create_review_record
print('ok')
"""


def test_scripts_import_without_database_libraries():
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    result = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
