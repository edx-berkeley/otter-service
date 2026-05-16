import base64
import json
import sys
import time
import signal
from functools import partial
import traceback
from datetime import datetime
import os
from hashlib import sha1
from oauthlib.oauth1.rfc5849 import signature, parameters
import pytz
from pytz import timezone
from jupyterhub.services.auth import HubOAuthenticated, HubOAuthCallbackHandler
from jupyterhub.utils import url_path_join
from lxml import etree
import aiohttp
import async_timeout
import tornado.web
import tornado.httpserver
import tornado.ioloop
import tornado.escape
import tornado.options
import tornado.gen
from otter_service import access_sops_keys
from otter_service.grade_assignment import grade_assignment
import firebase_admin
from firebase_admin import credentials, firestore
import grpc
from google.cloud.firestore_v1.client import firestore_client
from google.cloud.firestore_v1.client import firestore_grpc_transport
import otter_service.util as util


PREFIX = os.environ.get('JUPYTERHUB_SERVICE_PREFIX', '/services/otter_grade/')

# Use the application default credentials
cred = credentials.ApplicationDefault()
firebase_admin.initialize_app(cred, {
    'projectId': os.environ.get("GCP_PROJECT_ID"),
    'storageBucket': 'data8x-scratch.appspot.com/submissions'
})


def _get_firestore_collection(collection):
    db = firestore.client()
    if os.getenv("ENVIRONMENT") == "otter-docker-local-test":
        channel = grpc.insecure_channel("host.docker.internal:8080")
        transport = firestore_grpc_transport.FirestoreGrpcTransport(channel=channel)
        db._firestore_api_internal = firestore_client.FirestoreClient(transport=transport)
    return db.collection(collection)


def _emit_local_log(level, message, data=None, err=None):
    stream = sys.stderr if level in ("error", "warning") else sys.stdout
    details = [f"[otter-service:{level}] {message}"]

    if err is not None:
        details.append(f"error={err}")

    if data is not None:
        try:
            details.append(f"data={json.dumps(data, default=str)}")
        except TypeError:
            details.append(f"data={data}")

    print(" | ".join(details), file=stream, flush=True)


def write_grade(grade_info):
    """
    write the grade to the database

    :param grade_info: the four values in a tuple(userid, grade, section, lab, timestamp)
    :return Google FireStore document id
    """
    try:
        db = firestore.client()
        data = {
            'user': grade_info["userid"],
            'grade': grade_info["grade"],
            'course': grade_info["course"],
            'section': grade_info["section"],
            'assignment': grade_info["assignment"],
            'timestamp': get_timestamp()
        }
        return db.collection(f'{os.environ.get("ENVIRONMENT")}-grades').add(data)
    except Exception as err:
        raise Exception(f"Error inserting into Google FireStore for the following record: {grade_info}") from err


class GradePostException(Exception):
    """
    custom Exception to throw for problems when you post grade
    """
    def __init__(self, message=None):
        self.message = message
        super().__init__(self.message)


class GradeSubmissionException(Exception):
    """
    custom Exception to throw for problems with the submission itself
    """
    def __init__(self, message=None):
        self.message = message
        super().__init__()


def create_post_url(course_id, assignment_id):
    """
    This creates post url used to save the grade to edx
    :param course_id: the course id for this notebook
    :param assignment_id:  the assignment id for this notebook
    :return: formatted string
    """
    post_url = f"https://{os.environ['EDX_URL']}/courses/course-v1:{course_id}/xblock/block-v1:{course_id}+type@lti_consumer+block@{assignment_id}"
    post_url += "/handler_noauth/outcome_service_handler"
    return post_url


def create_sourced_id(course_id, assignment_id):
    """
    This creates the string representing the source id used by EdX
    :param course_id: the course id for this notebook
    :param assignment_id:  the assignment id for this notebook
    :return: formatted string
    """
    return f"course-v1%3A{course_id}:{os.environ['EDX_URL']}-{assignment_id}"


async def post_grade(solutions_base_path, metadata):
    """
    This posts the grade to the LTI server
    :paraem solutions_base_path: where the config file is to find the assignment id
    :param metadata:
        - userid: the user to post the grade for
        - grade: the grade as a float
        - course: the course this notebook is in
        - section: the section this notebook is in
        - assignment:  the assignment name for this notebook
    """
    body_hash = ""
    consumer_key = ""
    try:
        # post grade to EdX
        course_config = util.get_course_config(solutions_base_path)
        course = metadata["course"]
        section = metadata["section"]
        assignment = metadata["assignment"]
        user_id = metadata["userid"]
        grade = metadata["grade"]

        course_id = course_config[course][section]["course_id"]
        assignment_id = course_config[course][section]["assignments"][assignment]

        log_info_csv(user_id, metadata, f"Edx Course Config Loaded: Course Id: {course_id}, Assignment_id: {assignment_id}")

        sourced_id = create_sourced_id(course_id, assignment_id)
        outcomes_url = create_post_url(course_id, assignment_id)

        # TODO: extract this into a real library with real XML parsing
        # WARNING: You can use this only with data you trust! Beware, etc.
        post_xml = r"""
        <?xml version = "1.0" encoding = "UTF-8"?>
        <imsx_POXEnvelopeRequest xmlns = "http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0">
          <imsx_POXHeader>
            <imsx_POXRequestHeaderInfo>
              <imsx_version>V1.0</imsx_version>
              <imsx_messageIdentifier>999999123</imsx_messageIdentifier>
            </imsx_POXRequestHeaderInfo>
          </imsx_POXHeader>
          <imsx_POXBody>
            <replaceResultRequest>
              <resultRecord>
                <sourcedGUID>
                  <sourcedId>{sourcedid}</sourcedId>
                </sourcedGUID>
                <result>
                  <resultScore>
                    <language>en</language>
                    <textString>{grade}</textString>
                  </resultScore>
                </result>
              </resultRecord>
            </replaceResultRequest>
          </imsx_POXBody>
        </imsx_POXEnvelopeRequest>
        """
        secrets_file = os.path.join(os.path.dirname(__file__), "secrets/gke_key.yaml")
        consumer_key = access_sops_keys.get(None, "LTI_CONSUMER_KEY", secrets_file=secrets_file)
        consumer_secret = access_sops_keys.get(None, "LTI_CONSUMER_SECRET", secrets_file=secrets_file)

        sourced_id = f"{sourced_id}:{user_id}"
        post_data = post_xml.format(grade=float(grade), sourcedid=sourced_id)

        # Yes, we do have to use sha1 :(
        body_hash_sha = sha1()
        body_hash_sha.update(post_data.encode('utf-8'))
        body_hash = base64.b64encode(body_hash_sha.digest()).decode('utf-8')
        args = {
            'oauth_body_hash': body_hash,
            'oauth_consumer_key': consumer_key,
            'oauth_timestamp': str(time.time()),
            'oauth_nonce': str(time.time())
        }
        base_string = signature.signature_base_string(
            'POST',
            outcomes_url,
            signature.normalize_parameters(
                signature.collect_parameters(body=args, headers={})
            )
        )

        oauth_signature = signature.sign_hmac_sha1(base_string, consumer_secret, None)
        args['oauth_signature'] = oauth_signature

        headers = parameters.prepare_headers(args, headers={
            'Content-Type': 'application/xml'
        })

        async with async_timeout.timeout(10):
            async with aiohttp.ClientSession() as session:
                async with session.post(outcomes_url, data=post_data, headers=headers) as response:
                    resp_text = await response.text()

                    if response.status != 200:
                        raise GradePostException(response)

        response_tree = etree.fromstring(resp_text.encode('utf-8'))

        # XML and its namespaces. UBOOF!
        status = response_tree.find('.//{http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0}imsx_statusInfo')
        code_major = status.find('{http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0}imsx_codeMajor').text
        desc = status.find('{http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0}imsx_description').text

        log_info_csv(user_id, metadata, f"Posted to Edx: code: {code_major}, desc: {desc}")

        if code_major != 'success':
            raise GradePostException(desc)

    except GradePostException as grade_post_exception:
        raise grade_post_exception
    except Exception as ex:
        raise Exception(f"Problem Posting Grade to LTI:{ex}") from ex


class OtterHandler(HubOAuthenticated, tornado.web.RequestHandler):
    async def get(self):
        self.write("This is a post only page. You probably shouldn't be here!")
        user = self.get_current_user()
        self.write(json.dumps(user, indent=1, sort_keys=True))

    async def post(self):
        user = {}
        metadata = {
            "course": "not-set-yet",
            "section": None,
            "assignment": None
        }
        notebook = None
        timestamp = get_timestamp()
        using_test_user = False
        try:
            # Accept notebook submissions, saves, then grades them
            user = self.get_current_user()
            log_info_csv("PRINT USER OBJ", metadata, str(user))
            if user is None:
                url_referer = self.request.headers.get("Referer")
                if url_referer is None:
                    user = {"name": os.getenv("TEST_USER")}
                else:
                    user = {"name": url_referer.split("/")[4]}
                using_test_user = True
            req_data = tornado.escape.json_decode(self.request.body)
            # in the future, assignment should be metadata in notebook
            if 'nb' in req_data:
                notebook = req_data['nb']

            otter_check = "otter_service" in notebook['metadata']
            if otter_check and "course" in notebook['metadata']["otter_service"]:
                metadata["course"] = notebook['metadata']["otter_service"]['course']

            if otter_check and "section" in notebook['metadata']["otter_service"]:
                metadata["section"] = notebook['metadata']["otter_service"]['section']

            if otter_check and "assignment" in notebook['metadata']["otter_service"]:
                metadata["assignment"] = notebook['metadata']["otter_service"]['assignment']

            if not otter_check:
                err_msg = "Notebook does not have required \"otter_service\" attribute in metadata"
                raise GradeSubmissionException(err_msg)

            if metadata["course"] is None or \
                metadata["section"] is None or \
                    metadata["assignment"] is None:
                err_msg = "Notebook does not have required metadata in otter_service attribute: course, section, and assignment"
                raise GradeSubmissionException(err_msg)

            log_info_csv(user["name"], metadata, f"User logged in -  Using Referrer: {using_test_user}")
            # save notebook submission with user id and time stamp - this will be deleted
            sub_timestamp = timestamp.replace(" ", "-").replace(",", "-").replace(":", "-")
            submission_file = f"/tmp/{user['name']}_{metadata['course']}_{metadata['section']}_{metadata['assignment']}_{sub_timestamp}.ipynb"
            with open(submission_file, 'w', encoding="utf8") as outfile:
                json.dump(notebook, outfile)
            save_submission(user['name'], metadata, notebook)
            log_info_csv(user["name"], metadata, "user submission saved to logs")

            metadata["name"] = user["name"]
            metadata["submission_file"] = submission_file
            metadata["timestamp"] = str(timestamp)

            tornado.ioloop.IOLoop.current().spawn_callback(self._grade_and_post, metadata)

            # Let user know their submission was received
            self.write("Your submission is being graded and will be posted to the gradebook once it is finished running!")
            self.finish()
        except GradeSubmissionException as grade_submission_exception:
            if notebook is None:
                metadata["section"] = "No notebook was in the request body"
                metadata["assignment"] = "No notebook was in the request body"
            else:
                if metadata["section"] is None:
                    metadata["section"] = "Section: problem getting section - not in notebook?"
                if metadata["assignment"] is None:
                    metadata["assignment"] = "Assignment: problem getting assignment - not in notebook?"

            log_error_csv(user['name'], metadata, str(grade_submission_exception))
        except Exception as ex:  # pylint: disable=broad-except
            msg = ''.join(traceback.format_exception(etype=type(ex), value=ex, tb=ex.__traceback__))
            log_error_csv(user, metadata, msg)

    async def _grade_and_post(self, args):
        """
        This is called by spawn_callback method on the IOLoop to move the actual grading and
        posting of the grade out of the main thread.

        :param args
            - course
            - section
            - assignment
            - autograder_subpath
            - name
            - submission_file
            - timestamp
        """
        course = args["course"]
        section = args["section"]
        assignment = args["assignment"]
        name = args["name"]
        file_path = args["submission_file"]
        try:
            # Grade assignment
            grade, solutions_base_path = await grade_assignment(file_path, args)
            log_info_csv(name, args, f"Grade: {grade}")
            # Write the grade to a Firestore
            grade_info = {
                "userid": name,
                "course": course,
                "grade": grade,
                "section": section,
                "assignment": assignment
            }
            write_grade(grade_info)
            log_info_csv(name, args, f"Grade Written to database: {grade}")

            if os.getenv("POST_GRADE").lower() in ("true", '1', 't'):
                await post_grade(solutions_base_path, grade_info)
            else:
                log_info_csv(name, args, f"Grade NOT posted to EdX on purpose: {grade}")
                raise GradePostException("NOT POSTING Grades on purpose; see deployment-config-encrypted.yaml -- POST_GRADE")
        except GradePostException as gp:
            log_error_csv(name, args, str(gp))
        except Exception as ex:
            log_error_csv(name, args, str(ex))
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)


def get_timestamp():
    """
    returns the time stamp in PST Time
    """
    date_format = "%Y-%m-%d %H:%M:%S,%f"
    date = datetime.now(tz=pytz.utc)
    date = date.astimezone(timezone('US/Pacific'))
    return date.strftime(date_format)[:-3]


def write_logs(username, data, msg, trace, type, collection):
    if os.getenv("VERBOSE_LOGGING") == "True" or type == "error":
        payload = {
            'user': username,
            'course': data["course"],
            'section': data["section"],
            'assignment': data["assignment"],
            'message': msg,
            'trace': trace,
            'type': type,
            'timestamp': get_timestamp()
        }
        try:
            return _get_firestore_collection(collection).add(payload)
        except Exception as err:
            _emit_local_log(
                "error" if type == "error" else "warning",
                f"Skipping Firestore log write for {type}",
                data=payload,
                err=err,
            )
            return None


def log_info_csv(username, data, msg):
    """
    This logs information in the chain of events -- user logs in and submits,
    graded, posted to Edx

    :param username: the username
    :param data: json struct of data
        - course: the course
        - section: the section
        - assignment: the assignment
    :param msg: optional message to logs
    """
    try:
        write_logs(username, data, msg, None, "info", f'{os.environ.get("ENVIRONMENT")}-logs')
    except Exception as e:
        raise e


def log_error_csv(username, data, msg):
    """
    This logs the errors occurring when posting assignments

    :param timestamp: when the error occurred
    :param username: the username
    :param data: json struct of data
        - course: the course
        - section: the section
        - assignment: the assignment
    :param msg: optional message to logs
    """
    try:
        write_logs(username, data, msg, str(traceback.format_exc()), "error", f'{os.environ.get("ENVIRONMENT")}-logs')
    except Exception as e:
        raise e


def log_tornado_issues(msg, type):
    """
    This logs the errors associated with tornado

    :param msg: message about error
    """
    st = str(traceback.format_exc())
    st = st if "None" not in st else None
    data = {
        'message': msg,
        'trace': st,
        'type': type,
        'timestamp': get_timestamp()
    }

    try:
        collection = f'{os.environ.get("ENVIRONMENT")}-tornado-logs'
        return _get_firestore_collection(collection).add(data)
    except Exception as err:
        _emit_local_log(
            "error" if type == "error" else "warning",
            f"Skipping Firestore tornado log for {type}: {msg}",
            data=data,
            err=err,
        )
        return None


def save_submission(username, data, notebook):
    """
    This logs the errors associated with tornado

    :param username: username
    :param data: json struct of data
        - course: the course
        - section: the section
        - assignment: the assignment
        - autograder_materials_repo: the repo containing the autograder.zip
        - autograder_subpath: the path in the repo to autograder.zip
    :notebook: the cells in the notebook!
    """
    payload = dict(data)
    payload['user'] = username
    payload['notebook'] = notebook
    payload['timestamp'] = get_timestamp()

    try:
        collection = f'{os.environ.get("ENVIRONMENT")}-submissions'
        return _get_firestore_collection(collection).add(payload)
    except Exception as err:
        _emit_local_log(
            "warning",
            f"Skipping Firestore submission write for {username}",
            data=payload,
            err=err,
        )
        return None


def sig_handler(server, sig, frame):
    io_loop = tornado.ioloop.IOLoop.instance()
    MAX_WAIT_SECONDS_BEFORE_SHUTDOWN = 3

    def stop_loop(deadline):
        io_loop.stop()
        log_tornado_issues('Shutdown finally', "info")

    def shutdown():
        log_tornado_issues('Stopping http server', "info")
        server.stop()
        log_tornado_issues(f'Will shutdown in {MAX_WAIT_SECONDS_BEFORE_SHUTDOWN} seconds ...', "info")
        stop_loop(time.time() + MAX_WAIT_SECONDS_BEFORE_SHUTDOWN)

    log_tornado_issues(f'Caught signal: {sig}', "warning")
    io_loop.add_callback_from_signal(shutdown)


def start_server():
    """
    start the tornado server listening on port 10101 - I seperated this function from the main()
    in order to make testing easier
    :return: the application tornado object
    """
    tornado.options.parse_command_line()
    app = tornado.web.Application(
        [
            (PREFIX, OtterHandler),
            (
                url_path_join(
                    PREFIX, 'oauth_callback'
                ),
                HubOAuthCallbackHandler
            )
        ],
        cookie_secret=os.urandom(32)
    )

    server = tornado.httpserver.HTTPServer(app)
    server.listen(10101)
    log_tornado_issues('Server started', "info")
    signal.signal(signal.SIGTERM, partial(sig_handler, server))
    signal.signal(signal.SIGINT, partial(sig_handler, server))

    return app


def main():
    """
    start tornado
    """
    try:
        start_server()
        tornado.ioloop.IOLoop.current().start()
    except Exception:
        log_tornado_issues("Server start up issues", "error")


if __name__ == '__main__':
    main()
