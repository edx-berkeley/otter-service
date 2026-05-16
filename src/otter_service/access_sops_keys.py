import os
import subprocess
import yaml


def get(course_key, key_name, sops_path=None, secrets_file=None) -> str:
    """
    This method first tries to decrypt sops file and then tries the
    environment. it will return an Exception if the key is not found

    :param course_key: the course to find in Google Secrets Manager
    :param key_name: the name of the key to retrieve from the Secrets or the environment
    :param secrets_file: the name of the secrets file associated with the key
    :return: the secret
    """
    try:
        secret = get_via_env(course_key, key_name)
        if secret is None:
            secret = get_via_sops(course_key, key_name, sops_path=sops_path, secrets_file=secrets_file)
        return secret
    except Exception as ex:
        raise Exception(f"Key not decrypted: {key_name}; please configure: Error: {ex}") from ex


def get_via_sops(course_key, key, sops_path=None, secrets_file=None):
    """
    This function attempts to use sops to decrpyt the secrets/{secrets_file}

    :param course_key: the course to find in Google Secrets Manager
    :param key: the key to find in the course in Google Secrets Manager
    :param sops_path: [OPTIONAL] used to execute pytests
    :param secrets_file: [OPTIONAL] used to execute pytests
    :return: the value of the key or None
    """
    try:
        if sops_path is None:
            sops_path = "/usr/local/bin/sops"

        secrets_file = secrets_file

        sops_output = subprocess.check_output([sops_path, "-d", secrets_file], stderr=subprocess.STDOUT)
        dct = yaml.safe_load(sops_output)
        if course_key is None:
            return dct[key]
        return dct[course_key][key]
    except Exception as ex:
        raise ex


def get_via_env(course_key, key):
    """
    This checks the environment for the key.

    :param course_key: the course to find in Google Secrets Manager
    :param key: the key to find in the environment
    :return: the value of the key or None
    """
    if key in os.environ:
        if course_key is None:
            return os.environ[key]
        return os.environ[course_key][key]
    return None
