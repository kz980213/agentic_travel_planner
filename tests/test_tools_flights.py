import pytest

from travel_agent.tools.flights import (
    AIRLINE_MAP,
    AmadeusTokenCache,
    _aviasales_deeplink,
    book_flight,
    search_flights,
)


async def test_search_flights_falls_back_to_mock_without_keys():
    # conftest.py removes FLIGHT_API_KEY/SECRET — search hits mock path.
    results = await search_flights("JFK", "LHR", "2026-06-15")
    assert len(results) == 3
    for r in results:
        assert r["origin"] == "JFK"
        assert r["destination"] == "LHR"
        assert r["airline_code"] in AIRLINE_MAP
        # #7: mock results must be marked so the LLM won't present them as live prices.
        assert r["source"] == "mock"


async def test_search_flights_localized_currency():
    results = await search_flights("LHR", "JFK", "2026-06-15")
    assert results[0]["currency"] == "GBP"


async def test_search_flights_real_amadeus_path(monkeypatch):
    import httpx
    import respx

    from travel_agent.config import Config
    from travel_agent.tools import flights as flights_mod

    monkeypatch.setattr(Config, "FLIGHT_API_KEY", "k")
    monkeypatch.setattr(Config, "FLIGHT_API_SECRET", "s")
    monkeypatch.setattr(flights_mod, "_amadeus_token_cache", flights_mod.AmadeusTokenCache())

    with respx.mock:
        respx.post("https://test.api.amadeus.com/v1/security/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})
        )
        respx.get("https://test.api.amadeus.com/v2/shopping/flight-offers").mock(
            return_value=httpx.Response(200, json={"data": [
                {
                    "id": "OFFER1",
                    "itineraries": [{"duration": "PT7H", "segments": [{
                        "carrierCode": "DL", "number": "100",
                        "departure": {"at": "2026-06-15T08:00:00"},
                        "arrival": {"at": "2026-06-15T15:00:00"},
                    }]}],
                    "price": {"total": "500.00", "currency": "USD"},
                }
            ]})
        )
        results = await flights_mod.search_flights("JFK", "LHR", "2026-06-15")

    assert len(results) == 1
    assert results[0]["flight_id"] == "OFFER1"
    assert results[0]["airline_code"] == "DL"
    assert results[0]["price"] == 500.0
    # #7: live Amadeus data must be marked source="live".
    assert results[0]["source"] == "live"


async def test_book_flight_returns_real_deeplink_and_intent_ref():
    r = await book_flight("JFK", "LHR", "2026-10-20", "Alice", "DL393", passengers=2)
    assert r["status"] == "pending_user_action"
    assert r["intent_reference"].startswith("BK")
    assert "JFK2010LHR2" in r["booking_url"]
    # #8: the selected flight_id must be recorded on the reservation intent.
    assert r["flight_id"] == "DL393"


async def test_book_flight_requires_flight_id():
    # #8: booking without a selected flight_id is rejected, not silently routed.
    with pytest.raises(ValueError, match="flight_id"):
        await book_flight("JFK", "LHR", "2030-01-01", "Alice", "")


async def test_book_flight_rejects_invalid_date():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        await book_flight("JFK", "LHR", "tomorrow", "Alice", "DL1")


async def test_book_flight_rejects_past_date():
    with pytest.raises(ValueError, match="past"):
        await book_flight("JFK", "LHR", "2000-01-01", "Alice", "DL1")


async def test_book_flight_rejects_zero_passengers():
    with pytest.raises(ValueError, match="passengers"):
        await book_flight("JFK", "LHR", "2030-01-01", "Alice", "DL1", passengers=0)


def test_aviasales_deeplink_format():
    url = _aviasales_deeplink("JFK", "LHR", "2026-10-20", 1)
    assert url.endswith("/JFK2010LHR1") or "JFK2010LHR1" in url


async def test_amadeus_token_cache_reuses_token():
    import httpx
    import respx

    cache = AmadeusTokenCache()
    with respx.mock:
        route = respx.post("https://test.api.amadeus.com/v1/security/oauth2/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})
        )
        t1 = await cache.get("k", "s")
        t2 = await cache.get("k", "s")
    assert t1 == t2 == "tok-1"
    assert route.call_count == 1
