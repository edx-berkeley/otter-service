"""Tests for otter_service.keys (env-var + course-repo lookups)."""
import os

import pytest

from otter_service import keys


@pytest.fixture
def lti_env():
    os.environ["LTI_CONSUMER_KEY"] = "TEST_ENV_KEY"
    yield
    os.environ.pop("LTI_CONSUMER_KEY", None)


def test_get_env_returns_value(lti_env):
    assert keys.get_env("LTI_CONSUMER_KEY") == "TEST_ENV_KEY"


def test_get_env_raises_when_missing():
    os.environ.pop("THIS_KEY_DOES_NOT_EXIST", None)
    with pytest.raises(KeyError):
        keys.get_env("THIS_KEY_DOES_NOT_EXIST")


def test_get_course_repo_returns_known_courses():
    # Match the bundled course_repos.yaml.
    assert keys.get_course_repo("8x") == "github.com/edx-berkeley/8X-autograders"
    assert keys.get_course_repo("88ex") == "github.com/edx-berkeley/88E-autograders"
    assert keys.get_course_repo("88bx") == "github.com/edx-berkeley/88B-autograders"
    assert keys.get_course_repo("88cx") == "github.com/edx-berkeley/88C-autograders"


def test_get_course_repo_unknown_course_raises():
    with pytest.raises(KeyError):
        keys.get_course_repo("not-a-real-course")
