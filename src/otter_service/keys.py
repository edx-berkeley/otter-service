"""
Runtime credential and course-config lookups.

Credentials (GitHub App, LTI consumer key/secret, JupyterHub API token) are
expected in env vars, set by the deploy workflow from a K8s Secret. See
secrets-vars-cleanup-plan.md §4b for the migration that replaced
SOPS-encrypted in-package YAML with this pattern.

Per-course `autograder_repo` URLs are not secret — they're bundled in
``course_repos.yaml`` and read at runtime.
"""
import os
from pathlib import Path

import yaml


_COURSE_REPOS_PATH = Path(__file__).parent / "course_repos.yaml"
_course_repos_cache = None


def get_env(key: str) -> str:
    """Return the value of an env var, raising KeyError if unset.

    Used for GitHub App credentials (``github_app_id``,
    ``github_app_private_key``, ``github_app_installation_id``), LTI
    consumer key/secret (``LTI_CONSUMER_KEY``, ``LTI_CONSUMER_SECRET``),
    and the JupyterHub API token (``JUPYTERHUB_API_TOKEN``).
    """
    return os.environ[key]


def get_course_repo(course: str) -> str:
    """Look up the autograder GitHub repo URL for a course."""
    global _course_repos_cache
    if _course_repos_cache is None:
        _course_repos_cache = yaml.safe_load(_COURSE_REPOS_PATH.read_text())
    return _course_repos_cache[course]["autograder_repo"]
