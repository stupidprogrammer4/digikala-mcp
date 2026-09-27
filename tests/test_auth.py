"""Synthetic auth responses only; never include a real account or session."""

import json

import httpx
import pytest

from src.infra.auth import check_login


def test_otp_default_does_not_hide_available_password_login():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("authenticate/"):
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "has_account": True,
                        "has_password": True,
                        "login_method": "otp",
                    },
                },
            )
        if request.url.path.endswith("password/"):
            assert json.loads(request.content)["password"] == "test-password"
            return httpx.Response(
                200,
                json={"status": 200, "data": {}},
                headers={"Set-Cookie": "test_session=fake-token; Path=/; Secure"},
            )
        assert "test_session=fake-token" in request.headers["cookie"]
        return httpx.Response(200, json={"status": 200, "data": {"is_logged_in": True}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_login(client, "test-account", "test-password")
    assert result["state"] == "connected"
    assert result["session_persisted"] is False
    assert len(paths) == 3
    assert "fake-token" not in json.dumps(result)


def test_invalid_password_is_not_retried():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if len(paths) == 1:
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "has_account": True,
                        "has_password": True,
                    },
                },
            )
        return httpx.Response(400, json={"status": 400, "message": "رمز عبور نامعتبر است"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_login(client, "test-account", "test-password")
    assert len(paths) == 2
    assert result["reason"] == "invalid_password"
    assert result["state"] == "not_connected"


@pytest.mark.parametrize(
    "confirmation,expected", [(True, "challenge_required"), (False, "not_connected")]
)
def test_login_http_success_is_not_session_verification(confirmation, expected):
    def handler(request):
        if request.url.path.endswith("authenticate/"):
            data = {"has_account": True, "has_password": True}
        elif request.url.path.endswith("password/"):
            data = {"should_confirm_phone": confirmation}
        else:
            data = {"is_logged_in": False}
        return httpx.Response(200, json={"status": 200, "data": data})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_login(client, "test-account", "test-password")
    assert result["state"] == expected


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_sensitive_error_bodies_are_not_returned(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"message": "test-account test-password private-token"},
            headers={"Location": "https://example.invalid/"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_login(client, "test-account", "test-password")
    assert len(calls) == 1
    assert "private-token" not in json.dumps(result)
    assert "test-password" not in json.dumps(result)
    assert "test-account" not in json.dumps(result)
    assert result["state"] == "not_connected"
