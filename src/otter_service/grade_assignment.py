import asyncio
import async_timeout
import jwt
import requests
import tarfile
import time
import os
import glob
from otter_service import keys
import shutil
import otter_service.util as util


def get_github_app_token(app_id, private_key_pem, installation_id):
    """
    Exchange GitHub App credentials for a short-lived installation access token.
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": str(app_id),
    }
    encoded_jwt = jwt.encode(payload, private_key_pem, algorithm="RS256")
    resp = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {encoded_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    resp.raise_for_status()
    return resp.json()["token"]


def download_autograder_materials(course, save_path=None):
    """
    Download the autograder materials archive for a course.

    Tries the 'main' branch first, then falls back to 'master'.

    :param course: course slug (matches a top-level key in course_repos.yaml)
    :param save_path: where to save the archive — defaults to '.'
    """
    branch = "main"
    app_id = keys.get_env("github_app_id")
    private_key = keys.get_env("github_app_private_key")
    installation_id = keys.get_env("github_app_installation_id")
    git_access_token = get_github_app_token(app_id, private_key, installation_id)
    autograder_materials_repo = keys.get_course_repo(course)
    # GitHub App tokens require the Authorization header; embedding in the URL is not supported.
    # repo format: "github.com/owner/repo" → extract "owner/repo" for the API path.
    repo_path = "/".join(autograder_materials_repo.split("/")[-2:])
    auth_headers = {
        "Authorization": f"token {git_access_token}",
        "Accept": "application/vnd.github+json",
    }
    materials_url = f"https://api.github.com/repos/{repo_path}/tarball/{branch}"

    download_path = "/tmp/materials.tar.gz"
    if save_path is None:
        save_path = "."
        download_path = "./materials.tar.gz"
    r = requests.get(materials_url, headers=auth_headers, stream=True, allow_redirects=True)
    if r.status_code != 200:
        branch = "master"
        materials_url = f"https://api.github.com/repos/{repo_path}/tarball/{branch}"
        r = requests.get(materials_url, headers=auth_headers, stream=True, allow_redirects=True)

    if r.status_code == 200:
        with open(download_path, 'wb') as f:
            f.write(r.raw.read())
        file = tarfile.open(download_path)
        # The top-level directory name inside the tarball varies by download method.
        # Read it directly from the archive rather than assuming a naming convention.
        top_level = {m.name.split("/")[0] for m in file.getmembers() if "/" in m.name}
        file.extractall(save_path)
        file.close()
        file_name = autograder_materials_repo.split("/")[-1]
        extracted_path = os.path.join(save_path, top_level.pop()) if top_level else f"{save_path}/{file_name}-{branch}"
        storage_path = f"{save_path}/{file_name}"
        if os.path.isdir(storage_path):
            shutil.rmtree(storage_path)
        os.rename(extracted_path, storage_path)
        os.remove(download_path)
    else:
        raise Exception(f"Unable to access: {autograder_materials_repo}")
    return storage_path


def remove_notebook(submission):
    files = glob.glob(f'{submission}')
    for f in files:
        if not os.path.isdir(f):
            try:
                os.remove(f)
            except Exception:
                pass


async def grade_assignment(submission, args, save_path=None):
    """
    Spin up a docker instance with otter, grade the submission, return the grade.

    :param submission: path to the notebook file to grade
    :param args: json containing metadata from notebook
        - course: course slug (matches a top-level key in course_repos.yaml)
        - section: key to course config
        - assignment: assignment name
    :param save_path: [OPTIONAL] override save dir — used by tests
    :return: grade, solutions_base_path
    :rtype: float, string
    """
    try:
        solutions_base_path = None
        if save_path is None:
            save_path = "/opt"
        solutions_base_path = download_autograder_materials(args["course"], save_path=save_path)

        course_config = util.get_course_config(solutions_base_path)

        autograder_subpath = course_config[args["course"]][args["section"]]["subpath_to_zips"]

        solutions_path = f'{solutions_base_path}/{autograder_subpath}/{args["assignment"]}-autograder.zip'
        command = [
            'otter', 'grade',
            '-n', f'{args["course"]}.{args["section"]}.{args["assignment"]}',
            '-a', solutions_path,
            submission
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # this is waiting for communication back from the process
        # some images are quite big and take some time to build the first
        # time through - like 20 min for otter-grader
        async with async_timeout.timeout(2000):
            stdout, stderr = await process.communicate()

        for line in stderr.decode('utf-8').split('\n'):
            if line.strip() == '':
                # Ignore empty lines
                continue
            if 'Killed' in line:
                # Our container was killed, so let's just skip this one
                raise Exception(f"Container was killed -- nothing will work: {submission}")

        grade = stdout.decode("utf-8").strip()
        if grade is None or grade == '':
            cmd = ' '.join(command)
            raise Exception(f"Unable to determine grade coming from otter on: {submission} using this commnad: {cmd}")

        return round(float(grade), 3), solutions_base_path
    except asyncio.TimeoutError:
        raise Exception(f'Grading timed out for {submission}')
    except Exception as e:
        raise e
    finally:
        remove_notebook(submission)
