"""Password login with sanitized outcomes and independent session verification."""

import httpx

API_ORIGIN = "https://api.digikala.com"
Summary = dict[str, str | int | bool]


def response_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def failed_response(stage: str, response: httpx.Response, body: dict) -> Summary:
    # Only return fixed diagnostic text, never the provider's response or credentials.
    reason = "upstream_rejected"
    if stage == "password_login" and body.get("message") == "رمز عبور نامعتبر است":
        reason = "invalid_password"
    elif response.status_code == 429:
        reason = "rate_limited"
    elif response.status_code in (401, 403):
        reason = "access_denied"
    return {
        "stage": stage,
        "state": "not_connected",
        "reason": reason,
        "http_status": response.status_code,
    }


def check_login(client: httpx.Client, username: str, password: str) -> Summary:
    try:
        response = client.post(
            API_ORIGIN + "/v1/user/authenticate/",
            json={"username": username, "otp_call": False, "hash": None},
            timeout=20,
            follow_redirects=False,
        )
        body = response_body(response)
        if response.status_code != 200 or body.get("status") != 200:
            return failed_response("authenticate", response, body)
        data = body.get("data")
        if not isinstance(data, dict) or type(data.get("has_account")) is not bool:
            return {"stage": "authenticate", "state": "not_connected", "reason": "unknown_schema"}
        if data["has_account"] is not True:
            return {"stage": "authenticate", "state": "not_connected", "reason": "no_account"}
        # The storefront permits switching from its default OTP screen to password login.
        if data.get("has_password") is not True:
            return {
                "stage": "authenticate",
                "state": "challenge_required",
                "reason": "password_not_available",
            }

        response = client.post(
            API_ORIGIN + "/v1/user/login/password/",
            json={"username": username, "password": password, "type": "password"},
            timeout=20,
            follow_redirects=False,
        )
        body = response_body(response)
        if response.status_code != 200 or body.get("status") != 200:
            return failed_response("password_login", response, body)
        data = body.get("data")
        if isinstance(data, dict) and data.get("should_confirm_phone") is True:
            return {
                "stage": "password_login",
                "state": "challenge_required",
                "reason": "phone_confirmation_required",
            }

        response = client.get(
            API_ORIGIN + "/v1/user/init/",
            params={"skip_keys[]": "cart"},
            timeout=20,
            follow_redirects=False,
        )
        body = response_body(response)
        if response.status_code != 200 or body.get("status") != 200:
            return failed_response("verify_session", response, body)
        data = body.get("data")
        if isinstance(data, dict) and data.get("is_logged_in") is True:
            return {"stage": "verify_session", "state": "connected", "session_persisted": False}
        return {"stage": "verify_session", "state": "not_connected", "reason": "session_unverified"}
    except httpx.RequestError:
        return {"stage": "network", "state": "not_connected", "reason": "network_error"}
