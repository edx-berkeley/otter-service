# otter-service

This repo contains a tornado flask app, intended to support Berkeley EdX Courses, that accepts .ipynb files and grades them in a dockerized environment. Assuming you are running a Jupyterhub, you can ask Jupyterhub to run this otter-service as a service; you also have the option to run it in a stand alone manner. Grades are logged to a gcloud Cloud Firestore as well as written back to Edx

A separate Jupyterlab extension, [otter-submit](https://github.com/edx-berkeley/otter-submit), presents a "Submit" button to the user in a notebook rendered in Jupyterlab. The button is configured to serialize and send the notebook to this otter-service as well as notify the the user of the successful submission.

# FireStore/Database setup

All grades and the grading process is logged to gcloud Cloud FireStore. During local testing with pytest, the tests remove the collection created after verifying the data was written. Local docker testing, does not delete the entries but the collection is called, otter-docker-local-test, and can be viewed and deleted by going to the gcloud console and navigating to Cloud Firebase or Cloud Firestore.

# Configuration

## Notebook(ipynb metadata)
The ipynb notebooks need to include the metadata for which assignment they are. There are three pieces of information that are relevant: the course name, section and lab. They should be nested in an "otter_service" attribute as shown below. These values correspond to the course-config.json file described in the section below. These are set in the metadata section of every notebook:
```
metadata:{
  "otter_service": {
    "course": "8x",
    "section": "1",
    "assignment": "lab01"
  }
  ...
}
```

## Course Configuration
Each course needs to provide two pieces of information to whomever is handling otter-service(right now: sean.smorris@berkeley.edu) that is deployed in the secrets file of
this application
1) The name of repository where the autograder.zip files are kept. (e.g. github.com/edx-berkeley/88E-autograders)

The GitHub App (`COURSE_CONTENT_READER_APP_ID`, `COURSE_CONTENT_READER_PRIVATE_KEY`, `COURSE_CONTENT_READER_INSTALLATION_ID`) handles access to autograder/student/solution repos — no personal access token needed.

Finally, the private repository where the autograder.zip files are stored needs to contain a file named: course-config.json. The file is structured like this:

(1) course name which matches the course name in every notebooks metadata<br>
(2) section which matches the section in every notebooks metadata - if only one section put '1'<br>
(3) Edx course id<br>
(4) subpath to each autograder.zip in private autograder.zip repo<br>
(5) each EDX assignment name and assignment Id -- the assignment name must match the notebook metadata

```
{
  "8x" : { //(1)
    "1": { //(2)
      "course_id": "BerkeleyX+Data8.1x+3T2022", //(3)
      "subpath_to_zips": "materials/x22/lab/1", //(4)
      "assignments": {  //(5)
        "lab01": "6333e19d6b4d46f88df671ba50f616d8",
        "lab02": "fbf6740d45094b9b977111d218969273",
        "lab03": "0c8f28bdc48d4231843f62b512d73638",
        "lab04": "8db69daf14cf4751a088106be912c0cd",
        "lab05": "4cb9e3491c284cd5ae29bb48219ee15b"
      }
    },
    "2":{
      "course_id": "BerkeleyX+Data8.2x+3T2022",
      "subpath_to_zips": "materials/x22/lab/2",
      "assignments": {
        "lab01": "7abc0025c10f4b8ab123dbc88d34faaf",
        ...
```
Note how the folder structure is mirrored in the course-config.json.

If you are not posting grades to an LTI server, than you do not need to worry about this.


## Test files
The autograder zip files and test notebooks live in the per-course autograder repos (e.g. `edx-berkeley/88E-autograders`). The GitHub App fetches them at grading time — no Dockerfile changes needed when assignments change.

## Docker Image
This just FYI. The Dockerfile pulls an image : 
```
docker pull ucbdsinfra/otter-grader
```
This image is used by otter-grader to run the containerized grading.

# EdX/LTI integration

The system posts the grade back to the EdX via LTI. You need to have the `LTI_CONSUMER_KEY` and `LTI_CONSUMER_SECRET` defined and encoded via `sops` for this to work correctly. The secrets are in `otter-service/secrets/gke_key.yaml`

# Deployment

otter-service runs in-cluster on the `edx` GKE cluster (GCP project `data8x-scratch`) in the `otter-prod` and `otter-staging` namespaces. The Helm chart lives in [edx-berkeley/edx-hub](https://github.com/edx-berkeley/edx-hub/tree/prod/otter-service). Deployment is via the `deploy-otter.yaml` GitHub Actions workflow in the edx-hub repo — push to `prod` branch or trigger manually from the Actions tab.

# Deployment Details:
## Rollback: 
If we deploy and find problems the quickest way to rollback the deployment is to look at the revision history and undo the deployment by deploying to a previous revision number:
- kubectl rollout history deployment otter-pod -n otter-prod
- kubectl rollout history deployment otter-pod -n otter-prod --revision=# <-- to see details like the version of the image used
- kubectl rollout undo deployment/otter-pod -n otter-prod --to-revision=#

## CI/CD

### Workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `python-app.yml` — `lint-and-build` job | `pull_request` targeting `main` | Runs `flake8` linting and validates the Docker build; does **not** require secrets |
| `python-app.yml` — `test` job | `pull_request_target` targeting `main` | Authenticates to GCP via Workload Identity Federation, runs the full `pytest` suite against a real Firestore, and posts a reminder comment on the PR to tag after merge |
| `docker-grade-check.yml` | `pull_request_target` | Builds and runs the Docker image, fetches test notebooks from autograder repos via GitHub App, and runs end-to-end grading tests; posts result to Slack |
| `release.yml` | Push of a version tag (`X.Y.Z`) | Creates a GitHub release with auto-generated changelog, publishes the Python package to PyPI, builds and pushes the Docker image via Google Cloud Build, and opens a PR in [edx-hub](https://github.com/edx-berkeley/edx-hub) to update the `otter-srv` image tag |

The `python-app.yml` workflow uses two separate triggers because `pull_request_target` is needed to safely access secrets for forks, while `pull_request` is used for the secret-free lint/build step.

### Releasing a new version

1. Merge all desired changes to `main`.
2. Bump the version in `otter-service/__init__.py` and update `CHANGELOG.md`.
3. Push a version tag:

```bash
git tag X.Y.Z
git push upstream X.Y.Z. <-- assuming your fork is origin
```

CI will create the GitHub release, publish to PyPI, build the Docker image via Cloud Build, and open a PR in `edx-hub` to roll out the new image.

### GCP Workload Identity (for the `test` job)

The `test` job in `python-app.yml` authenticates to GCP without a service account key using Workload Identity Federation:
- **Project:** `data8x-scratch` (project number `75088546496`)
- **Pool/Provider:** `otter-pool / github-actions`
- **Service account:** `otter-sa@data8x-scratch.iam.gserviceaccount.com`

### Slack notifications

CI results are posted to the **#edx-hub-ci** channel in the **UCB DS External** Slack workspace. To request access, contact a team member or reach out to sean.smorris@berkeley.edu.

The following workflows post to Slack on every run (success or failure):
- `docker-grade-check.yml` — on every PR

### Repository variables and secrets

The following must be configured on the repository (or the `edx-berkeley` organization) for CI to function:

**Variables (`vars.*`):**

| Name | Description |
|---|---|
| `EDX_IMAGE_BUILDER_APP_ID` | GitHub App ID used to open PRs in edx-hub |
| `OTTER_AUTOGRADERS_APP_ID` | GitHub App ID for reading autograder repos |
| `OTTER_AUTOGRADERS_INSTALLATION_ID` | Installation ID for the autograder GitHub App |

**Secrets (`secrets.*`):**

| Name | Description |
|---|---|
| `GCP_SA_KEY` | GCP service account JSON key (used by `docker-grade-check.yml`) |
| `GCP_PROJECT` | GCP project ID (e.g., `data8x-scratch`) |
| `OTTER_GH_APP_PRIVATE_KEY` | Private key for the GitHub App that reads autograder repos (shared with edx-user-image and xDevs) |
| `PRIVATE_KEY_SECRET` | Private key for the GitHub App that opens PRs in edx-hub |
| `SLACK_WEBHOOK_URL` | Incoming webhook URL for the `#edx-hub-ci` Slack channel |


# pytest
Run ./deployment-utils/local/pytest.sh -- this will start the Firestore emulator and run the tests.
If the emulator is already running it shuts it down. I shut down the emulator when the tests are done
as well but you could comment out this line to check out the data that was stored.

# Local installation for testing/developing

Install a FireStore Emulator so you test locally:
- Install FireStore CLI: https://firebase.google.com/docs/cli/#install-cli-mac-linux
- firebase login
- firebase projects:list
- firebase setup:emulators:firestore
- make java jdk installed
- firebase emulators:start --only firestore --project data8x-scratch
- You can see the UI here: http://localhost:4000/firestore
- python3 -m pip install google-cloud-firestore
- You will notice the re-direct in firebase_local fixture used by test_write_grade in test_otter_nb.py


With docker installed, you can use the `Dockerfile-dev` file to deploy a local instance of otter-service. The `deployment/local/build.sh` file gives some guidance to building and installing local changes to otter-service for testing. The usual process is to make changes, execute `build.sh`, which relies on a `docker-compose.yml` file. A sample is below but before we look, I would also study the file `tests/integration.py`. If you execute this file, you can test the service via a web connection. 

Sample docker-compose.yml:
```
version: "3.9"
services:
  app:
    image: otter-srv
    build:
      context: .
      dockerfile: Dockerfile-dev
      args:
        OTTER_SERVICE_VERSION: whatever_version you specify in otter-service/__init__.py
    env_file:
      - ../.local-env
    ports:
      - 10101:10101
    volumes:
      - /tmp/otter:/mnt/data
    entrypoint: ''

networks:
  default:
    driver: bridge
```

Notes:
- .local-env These are environment variables that must be set. They mirror the variables in the file `otter-service/values.yaml` under the key `otter_env`. You do not need to encrypt your local-env file with sops. 

# Typical Workflow
- Activate/create the python environment with conda or virtualenv using requirements/dev.txt
- Make changes on a feature branch
- Add tests to `tests` dir
- Run Tests: sh deployment-utils/local/pytest.sh
- Deploy Locally: sh deployment-utils/local/build.sh
- Run Integration Test: python3 tests/integration.py local 88e(or 8x) -- see file
- Check local firestore to see progress: http://localhost:4000/firestore
- Open a PR to the `staging` branch in [edx-berkeley/edx-hub](https://github.com/edx-berkeley/edx-hub) to deploy to staging (requires `STAGING_ENABLED=true`)
- Once verified on staging, open a PR from `staging` → `prod` in edx-hub to deploy to prod


# Service installation in JupyterHub

Instructions can be found here for running it as a service within your [jupyterhub](https://jupyterhub.readthedocs.io/en/stable/reference/services.html#launching-a-hub-managed-service)



