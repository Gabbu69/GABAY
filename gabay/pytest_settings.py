"""Settings entry point for the pytest suite.

pytest-django imports this module instead of ``gabay.settings`` so the test
profile is pinned before Django reads the environment. This keeps local runs
identical to CI regardless of how the developer's shell is configured.
"""

import os

os.environ.setdefault("GABAY_PROFILE", "test")
os.environ.setdefault("GABAY_SECRET_KEY", "test-only-gabay-secret-key-not-for-production")

from gabay.settings import *  # noqa: E402,F401,F403
from gabay.settings import DATA_DIR, DATABASES  # noqa: F401
