from unittest.mock import Mock

import pytest
import requests

from loc_maps.client import Client, FetchError, Paused, api_url


def clock_client(state, responses, budget=20):
    clock = [1000.0]

    def sleep(seconds):
        clock[0] += seconds

    session = Mock()
    session.headers = {}
    session.get.side_effect = responses
    return Client(state, budget, 10000, session, lambda: clock[0], sleep), clock


def response(status=200, content_type="application/json", payload=None):
    result = Mock(status_code=status, headers={"Content-Type": content_type})
    result.json.return_value = payload or {"item": {"title": "Map"}}
    return result


def test_global_limit_and_budget(state):
    client, clock = clock_client(state, [response(), response()], budget=2)
    client.get("https://www.loc.gov/maps/")
    client.get("https://www.loc.gov/maps/")
    assert clock[0] >= 1030.0
    with pytest.raises(Paused, match="budget"):
        client.get("https://www.loc.gov/maps/")


@pytest.mark.parametrize(
    "status,kind", [(429, "application/json"), (403, "application/json"), (200, "text/html")]
)
def test_captcha_and_rate_limit_persist_pause(state, status, kind):
    client, clock = clock_client(state, [response(status, kind)])
    with pytest.raises(Paused):
        client.get("https://www.loc.gov/maps/")
    assert state.get("pause_until") >= clock[0] + 3600
    with pytest.raises(Paused, match="cooldown"):
        client.get("https://www.loc.gov/maps/")
    assert client.session.get.call_count == 1


def test_truncated_response_retries_without_success(state):
    broken = response()
    broken.json.side_effect = ValueError("Truncated JSON")
    client, _ = clock_client(state, [broken] * 3)
    with pytest.raises(FetchError, match="three attempts"):
        client.get("https://www.loc.gov/maps/")
    assert client.session.get.call_count == 3


def test_network_failure_retries(state):
    client, _ = clock_client(state, [requests.Timeout(), response()])
    assert client.get("https://www.loc.gov/maps/")["item"]


def test_untrusted_next_url():
    with pytest.raises(ValueError):
        api_url("https://example.com/maps/")


def test_rate_limit_slows_next_session_and_recovers_gradually(state):
    client, clock = clock_client(state, [response(429)])
    with pytest.raises(Paused):
        client.get("https://www.loc.gov/maps/")
    assert state.get("request_interval") == 60.0
    state.set("pause_until", 0)
    state.set("next_request", 0)
    resumed, clock = clock_client(state, [response()] * 100, budget=100)
    for _ in range(100):
        resumed.get("https://www.loc.gov/maps/")
    assert clock[0] >= 1000 + 99 * 60.0 - 0.01
    assert state.get("request_interval") == pytest.approx(48.0)


@pytest.mark.parametrize("failure", [requests.Timeout(), response(500), response(503)])
def test_temporary_outages_save_cooldown_instead_of_stopping_chain(state, failure):
    client, clock = clock_client(state, [failure] * 3)
    with pytest.raises(Paused):
        client.get("https://www.loc.gov/maps/")
    assert state.get("pause_until") >= clock[0] + 300
    assert state.get("request_interval") == 60.0
    assert client.session.get.call_count == (
        1 if getattr(failure, "status_code", None) == 503 else 3
    )


def test_permanent_not_found_still_requires_inspection(state):
    client, _ = clock_client(state, [response(404)] * 3)
    with pytest.raises(FetchError):
        client.get("https://www.loc.gov/item/missing/")


def test_legacy_checkpoint_is_slowed_and_rest_follows_response(state):
    state.set("request_interval", 12.2)
    client, clock = clock_client(state, [])
    starts = []

    def slow_response(*args, **kwargs):
        starts.append(clock[0])
        clock[0] += 40
        return response()

    client.session.get.side_effect = slow_response
    client.get("https://www.loc.gov/maps/")
    client.get("https://www.loc.gov/maps/")
    assert starts == [1000, 1070]
    assert state.get("request_interval") == 30


def test_repeated_overload_extends_persisted_cooldown(state):
    client, clock = clock_client(state, [response(503), response(429)])
    with pytest.raises(Paused):
        client.get("https://www.loc.gov/maps/")
    assert state.get("pause_until") == clock[0] + 3600
    clock[0] = state.get("pause_until") + 1
    with pytest.raises(Paused):
        client.get("https://www.loc.gov/maps/")
    assert state.get("pause_until") == clock[0] + 7200
    assert client.session.get.call_count == 2


def test_long_retry_after_is_honored(state):
    overloaded = response(503)
    overloaded.headers["Retry-After"] = "172800"
    client, clock = clock_client(state, [overloaded])
    with pytest.raises(Paused):
        client.get("https://www.loc.gov/maps/")
    assert state.get("pause_until") == clock[0] + 172800


def test_successful_requests_never_speed_up_beyond_polite_floor(state):
    state.set("successful_requests", 99)
    client, _ = clock_client(state, [response()])
    client.get("https://www.loc.gov/maps/")
    assert state.get("request_interval") == 30
