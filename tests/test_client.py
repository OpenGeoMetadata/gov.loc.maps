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
    assert clock[0] >= 1006.1
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
    assert state.get("request_interval") == 12.2
    state.set("pause_until", 0)
    state.set("next_request", 0)
    resumed, clock = clock_client(state, [response()] * 100, budget=100)
    for _ in range(100):
        resumed.get("https://www.loc.gov/maps/")
    assert clock[0] >= 1000 + 99 * 12.2 - 0.01
    assert state.get("request_interval") == pytest.approx(9.76)
