"""
Integration tests that hit real GitHub via the course-content-reader App.

Require env vars:
  - github_app_id
  - github_app_private_key
  - github_app_installation_id

Set in CI from org-level secrets (COURSE_CONTENT_READER_* family); skipped
locally unless you export them. See secrets-vars-cleanup-plan.md §4b.
"""
import os
import shutil

import pytest

import otter_service.grade_assignment as ga


REQUIRED_ENV = ("github_app_id", "github_app_private_key", "github_app_installation_id")
requires_gh_creds = pytest.mark.skipif(
    not all(os.environ.get(k) for k in REQUIRED_ENV),
    reason=f"requires GitHub App credentials in env: {', '.join(REQUIRED_ENV)}",
)


@pytest.fixture()
def configure():
    yield
    for d in ("./8X-autograders", "./88E-autograders"):
        if os.path.isdir(d):
            shutil.rmtree(d)
    if os.path.isfile("./final_grades.csv"):
        os.remove("./final_grades.csv")


@pytest.mark.skip(reason=(
    "Depends on the SOPS-encrypted gh_key.yaml private key, which was "
    "invalidated when the course-content-reader App key was rotated on "
    "2026-05-17. Will be replaced by an env-var-driven test as part of "
    "the SOPS-removal workstream (see secrets-vars-cleanup-plan.md §4b)."
))
def test_download_autograder_materials(configure):
    ga.download_autograder_materials("8x", save_path=".")
    assert os.path.isdir("./8X-autograders")


@pytest.mark.asyncio
@pytest.mark.skip(reason="need to update to otter-grader 6")
async def test_grade_assignment_8x(configure):
    grade, _ = await ga.grade_assignment(
        "tests/test_files/lab01.ipynb",
        {"course": "8x", "section": "1", "assignment": "lab01"},
        save_path=".",
    )
    assert grade == 1.0


@pytest.mark.asyncio
@pytest.mark.skip(reason=(
    "Same SOPS-key dependency as test_download_autograder_materials — "
    "skipped pending the SOPS-removal workstream."
))
async def test_grade_assignment_88e(configure):
    grade, _ = await ga.grade_assignment(
        "tests/test_files/lab01-88e.ipynb",
        {"course": "88ex", "section": "1", "assignment": "lab01"},
        save_path=".",
    )
    assert grade == 1.0
