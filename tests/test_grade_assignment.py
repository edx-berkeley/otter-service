import pytest
import otter_service.grade_assignment as ga
import os
import shutil

# Point at the in-package secrets file so tests stay in sync with what
# the service actually uses at runtime (avoids a stale duplicate fixture).
KEY_FILE_PATH = "../src/otter_service/secrets/gh_key.yaml"


@pytest.fixture()
def configure():
    print("Starting tests")
    yield "resource"
    print("Removing Tree")
    if os.path.isdir("./8X-autograders"):
        shutil.rmtree("./8X-autograders")
    if os.path.isdir("./88E-autograders"):
        shutil.rmtree("./88E-autograders")
    if os.path.isfile("./final_grades.csv"):
        os.remove("./final_grades.csv")


@pytest.mark.skip(reason=(
    "Depends on the SOPS-encrypted gh_key.yaml private key, which was "
    "invalidated when the course-content-reader App key was rotated on "
    "2026-05-17. Will be replaced by an env-var-driven test as part of "
    "the SOPS-removal workstream (see secrets-vars-cleanup-plan.md §4b)."
))
def test_download_autograder_materials(configure):
    secrets_file = os.path.join(os.path.dirname(__file__), KEY_FILE_PATH)
    sops_path = "sops"
    ga.download_autograder_materials("8x", sops_path, secrets_file)
    assert os.path.isdir("./8X-autograders")


@pytest.mark.asyncio
@pytest.mark.skip(reason="need to update to otter-grader 6")
async def test_grade_assignment_8x(configure):
    secrets_file = os.path.join(os.path.dirname(__file__), KEY_FILE_PATH)
    args = {
        "course": "8x",
        "section": "1",
        "assignment": "lab01",
    }
    grade, _ = await ga.grade_assignment("tests/test_files/lab01.ipynb",
                                         args,
                                         sops_path="sops",
                                         secrets_file=secrets_file,
                                         save_path=".")
    assert 1.0 == grade


@pytest.mark.asyncio
@pytest.mark.skip(reason=(
    "Same SOPS-key dependency as test_download_autograder_materials — "
    "skipped pending the SOPS-removal workstream."
))
async def test_grade_assignment_88e(configure):
    secrets_file = os.path.join(os.path.dirname(__file__), KEY_FILE_PATH)
    args = {
        "assignment": "lab01",
        "course": "88ex",
        "section": "1",
    }
    grade, _ = await ga.grade_assignment("tests/test_files/lab01-88e.ipynb",
                                         args,
                                         sops_path="sops",
                                         secrets_file=secrets_file,
                                         save_path=".")
    assert 1.0 == grade
