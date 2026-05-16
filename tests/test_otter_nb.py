import pytest
import os
import otter_service.otter_nb as gn
from firebase_admin import firestore

import grpc
from google.cloud.firestore_v1.client import firestore_client
from google.cloud.firestore_v1.client import firestore_grpc_transport


@pytest.fixture
def app():
    return gn.start_server()


@pytest.fixture
def firebase_local():
    channel = grpc.insecure_channel("localhost:8080")
    transport = firestore_grpc_transport.FirestoreGrpcTransport(channel=channel)
    db = firestore.client()
    db._firestore_api_internal = firestore_client.FirestoreClient(transport=transport)
    yield db


@pytest.mark.skip(reason="test oauth2")
async def test_http_client(http_server_client):
    resp = await http_server_client.fetch('/services/otter_grade/')
    assert resp.code == 302
    http_server_client.close()


@pytest.mark.skip(reason="tests local firestore")
def test_write_grade(firebase_local):
    grade_info = {"userid": "WRITE_TEST", "course": "8x-test", "grade": 88.0, "section": "1", "assignment": "lab99"}
    doc = gn.write_grade(grade_info)[1]
    doc_ref = firebase_local.collection(f'{os.environ.get("ENVIRONMENT")}-grades').document(f'{doc.id}')
    doc_obj = doc_ref.get()
    doc_dict = doc_obj.to_dict()

    assert "WRITE_TEST" == doc_dict["user"]
    assert "1" == doc_dict["section"]
    assert 88.0 == doc_dict["grade"]
    assert "lab99" == doc_dict["assignment"]

    firebase_local.collection(f'{os.environ.get("ENVIRONMENT")}-grades').document(f'{doc.id}').delete()


def test_create_post_url():
    os.environ['EDX_URL'] = "edge.edx.org"
    url = gn.create_post_url("BerkeleyX+Data8.1x+2021", "6333e19d6b4d46f88df671ba50f616d8")
    assert "BerkeleyX+Data8.1x+2021/xblock/block-v1:BerkeleyX+Data8.1x+2021+type@lti_consumer+block@6333e19d6b4d46f88df671ba50f616d8" in url
    del os.environ['EDX_URL']


def test_create_sourced_id():
    os.environ['EDX_URL'] = "edge.edx.org"
    url = gn.create_sourced_id("BerkeleyX+Data8.1x+2021", "6333e19d6b4d46f88df671ba50f616d8")
    assert url == "course-v1%3ABerkeleyX+Data8.1x+2021:edge.edx.org-6333e19d6b4d46f88df671ba50f616d8"
    del os.environ['EDX_URL']


def test_log_tornado_issues_falls_back_without_firestore(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "ci-smoke-test")

    def raise_firestore_client():
        raise RuntimeError("missing firestore credentials")

    monkeypatch.setattr(gn.firestore, "client", raise_firestore_client)

    assert gn.log_tornado_issues("Server started", "info") is None

    captured = capsys.readouterr()
    assert "Skipping Firestore tornado log for info: Server started" in captured.err


def test_log_error_csv_falls_back_without_firestore(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "ci-smoke-test")

    def raise_firestore_client():
        raise RuntimeError("missing firestore credentials")

    monkeypatch.setattr(gn.firestore, "client", raise_firestore_client)

    gn.log_error_csv("tester", {"course": "88ex", "section": "1", "assignment": "lab01"}, "boom")

    captured = capsys.readouterr()
    assert "Skipping Firestore log write for error" in captured.err
