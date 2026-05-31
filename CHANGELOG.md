## 2.2.4

#### Bug fixes

- LTI 1.3 AGS dispatch in `OtterHandler.post` was gated on
  `not using_test_user`, which skipped the new code path for **every**
  real submission in this deployment — `self.get_current_user()`
  consistently returns None here, so the request handler always falls
  through to URL-referrer parsing (which sets `using_test_user=True`).
  Fixed by replacing the guard with an explicit check against the
  `TEST_USER` env var: real users (whether HubOAuth-identified or
  URL-derived) now reach the auth_state fetch + AGS dispatch.

## 2.2.3

#### Features

- New `otter_service.ags` module: LTI 1.3 Assignment & Grade Services
  (AGS) grade-passback. Signs a client-credentials JWT with the tool's
  RS256 private key, exchanges it for an OAuth2 access_token, and POSTs
  a Score JSON to `<lineitem>/scores` (PRs #48 + #49).
- `OtterHandler.post` now fetches the submitting user's `auth_state` via
  `/hub/api/users/<name>` (requires the `admin:auth_state` scope on the
  `otter_grade` service token, granted in edx-hub `common.yaml`), pulls
  the `lti13_ags` block (lineitem URL, token_url, client_id, sub), and
  dispatches to `ags.post_grade_lti13()` instead of the LTI 1.1
  XML/OAuth1 path. LTI 1.1 launches are unaffected — they fall through
  to the legacy path as before.

#### Configuration

- Tool's RSA private key read from `LTI13_PRIVATE_KEY` env var (full
  PEM) or `LTI13_PRIVATE_KEY_PATH` (path to a PEM file on disk).
- Optional `LTI13_KEY_ID` (`kid` header on the signed JWT) and
  `LTI13_CLIENT_ID` (fallback when the platform-issued client_id isn't
  available in auth_state) are also env-driven.

#### Release notes

- `2.2.2` was tagged but never published — the `__version__` bump was
  missed, so the PyPI upload failed with `File already exists` (the
  built wheel kept the `2.2.1` name). The `2.2.2` GitHub release and
  git tag are orphaned (no PyPI/GAR artifacts); `2.2.3` is the first
  release carrying the AGS changes.

## 2.2.1

#### Infrastructure

- `cloudbuild.yaml` now pushes the `otter-srv` image to Artifact Registry
  (`us-central1-docker.pkg.dev/data8x-scratch/otter-images/otter-srv`)
  instead of the deprecated `gcr.io/data8x-scratch/otter-srv`.

#### CI

- Added a weekly Firestore orphan sweeper for stale `otter-ci-*` collections
  left behind when GH Actions SIGKILLs the integration-test runner past its
  `timeout-minutes`.

## 2.2.0

#### Release process

- `release.yml` now runs the full grading test (Docker build + Firestore
  round-trip) before publishing to PyPI. A failed grading test blocks the
  PyPI upload.
- Added missing `PyJWT` and `requests` dependencies to the grading-test
  setup step (`tests/fetch_test_notebooks.py` imports them).

## 2.1.0

#### Breaking changes

- Runtime no longer reads credentials from SOPS-encrypted in-package YAML.
  GitHub App credentials (`github_app_id`, `github_app_private_key`,
  `github_app_installation_id`), LTI consumer key/secret, and the
  JupyterHub API token are now read from env vars set by the deploy
  workflow (edx-hub `deploy-otter.yaml`) from org-level GitHub secrets
  via Helm `--set-string` → K8s Secret → pod env.
- `grade_assignment.download_autograder_materials()` and `grade_assignment()`
  no longer accept `sops_path` / `secrets_file` parameters.
- The `secrets/` directory inside the package is gone.

#### Enhancements made

- New `keys` module replaces `access_sops_keys`: `get_env(key)` for env-var
  lookups, `get_course_repo(course)` for the public course→repo mapping.
- New `course_repos.yaml` (plain, not encrypted) bundles the public
  course→autograder-repo URLs that used to live in the SOPS file.
- Dockerfile no longer installs `sops` binary (smaller image, one less
  external dep).
- `tests/test_grade_assignment.py` integration tests now gate on env-var
  presence via `@pytest.mark.skipif` — run in CI when org-level
  `COURSE_CONTENT_READER_*` are populated, skip cleanly locally.
- `python-app.yml` workflow drops the SOPS install step.


## 2.0.18

#### Bug fixes

- Stop the otter-pr container before the Firestore CI-cleanup step so the
  pod's `Shutdown finally` log lands first instead of orphaning the
  per-run `otter-ci-{run_id}-tornado-logs` collection.

#### Maintenance

- Re-cut release after 2.0.17's tag was never pushed; image was lingering
  in GCR with the right sops-path fix but the auto-bumper still pointed
  edx-hub at 2.0.16.

## 2.0.17

#### Bug fixes

- Align `access_sops_keys` default `sops_path` with where the Dockerfile
  installs the binary (`/usr/local/bin/sops`). Without this fix, every
  call to `download_autograder_materials` raised
  `FileNotFoundError: '/root/go/bin/sops'` in 2.0.16.

## 2.0.16

#### Enhancements made

- removed dependency to firestore logging; although still logs if credentials correct

## 2.0.15

#### Enhancements made

- Release workflow now opens a PR to edx-hub with a human-readable tag when a new version is published

## 2.0.14

#### Maintenance

- No-op release to validate post-org-migration release pipeline (PyPI publish + GCR push via WIF)

## 2.0.13

#### Enhancements made

- Moved repo from data-8 to edx-berkeley GitHub org
- Updated WIF provider attribute condition to edx-berkeley
- Updated gcp-workload-identity.sh to include attribute condition on provider create
- Updated README: otter-submit link, deployment model, namespace names, GitHub App auth
- Added grading-test-plan.md

## 2.0.12

#### Bug fixes

- Fix GitHub App archive download: switch from `x-access-token` embedded in git URL (unsupported for App tokens) to `api.github.com/repos/{repo}/tarball/{branch}` with `Authorization` header
- Detect extracted tarball directory name from archive members instead of assuming `{repo}-{branch}` format (API tarballs extract as `{owner}-{repo}-{sha}`)

## 2.0.11

#### Enhancements made

- Switch Autograder copy to GH App

## 2.0.9

#### Enhancements made

- Backed out new - 88e-autograders

## 2.0.7

#### Enhancements made

- The autograder Configs are all updated
- GH Workflow is more efficient

## 2.0.4

#### Enhancements made

- Added 88be config

## 2.0.3

#### Enhancements made

- Updated to otter-grader 6.1.3

## 2.0.2

#### Enhancements made

- Updated to otter-grader 6.0.4
## 1.1.1

#### Enhancements made

- Remove OAuth

## 1.1.0

#### Enhancements made

- Updated Grade reporting to three decimals

## 0.2.10

#### Enhancements made

- otter-service handles any course from edx with appropriate config
- changes to how secrets are stored
- updates to logging

## 0.2.4

#### Enhancements made

- Ubuntu 22.04
- Cleaning OAuth, tornado libraries
- Flake8 Lint cleanUp
- Updated route to /services/otter_grade


## 0.1.75.13

#### Enhancements made

- cycled gh key

## 0.1.22

#### Enhancements made

- Changed the error handling on the GradePostException
- Changed deletion of materials directory to just before downloading

## 0.1.21

#### Enhancements made

- Save_Path are trailing forward slash

## 0.1.20

#### Enhancements made

- Fixed URI to include backslash

## 0.1.19

#### Enhancements made

- Configured the grading to download autotgrader materials each time so
that the materials can be changed and the system not re-deployed
- fixed image name in deployment.yaml; mistake in set-image path

## 0.1.18

#### Enhancements made

- cloud deploy not longer dependent on local build

## 0.1.17

#### Enhancements made

- deploys from branches to appropriate namespaces
- converted to otter-service(removed from gofer-service repo)

## 0.1.11

#### Enhancements made

- Configure GitHub Actions: releases, deployments
- Added init.py to src dir to run pytests from GH
- arrange test files Github Action to run tests
- Github Action configured to build off master
- Github Action configured to build docker image
- Github Action configured to push docker image and deploy to cluster
- Added Persistent Volume
- Added NFS


## 0.1

### 0.1.0 - 2021-10-23

#### Enhancements made

- swapped gofer-grader to otter-grader

#### Bugs fixed

- improved error handling to ensure we don't try to post a bad submission
