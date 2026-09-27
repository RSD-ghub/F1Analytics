"""Reading the figures a circuit's official page states about itself.

Facts only — name, length, laps, distance, first season, lap record. The track
illustrations on those pages are F1's artwork rather than measurements, and
this project publishes what it draws from telemetry instead. Nothing here
fetches or stores an image, and that is deliberate rather than incidental.
"""

from app.services.f1_site import parse_circuit_facts, season_slugs


#: A cut-down copy of the real payload's shape: server-rendered, with the data
#: inlined as escaped JSON rather than fetched by script.
PAGE = r'''
<html><body><script>self.__next_f.push([1,"{\"circuitOfficialName\":\"Sepang International Circuit\",\"circuitShortName\":\"Kuala Lumpur\",\"circuitLocation\":
\"Malaysia\",\"circuitType\":\"Permanent\",\"trackLength\":\"5.543\",
\"scheduledLapCount\":\"56\",\"scheduledDistance\":\"310.398\",
\"venueFirstSeason\":\"1999\",\"fastestLapTime\":\"1:34.080\",
\"fastestLapDriver\":\"Sebastian Vettel\",\"fastestLapSeason\":\"2017\",
\"fastestLapTeam\":\"Scuderia Ferrari\",\"meetingOfficialName\":
\"FORMULA 1 GULF AIR BAHRAIN GRAND PRIX IN MALAYSIA 2026\"}"])</script></body></html>
'''.replace("\n", "")


def test_the_stated_figures_are_read_off_the_page():
    facts = parse_circuit_facts(PAGE)

    assert facts["official_name"] == "Sepang International Circuit"
    assert facts["short_name"] == "Kuala Lumpur"
    assert facts["length_km"] == 5.543
    assert facts["scheduled_laps"] == 56
    assert facts["race_distance_km"] == 310.398
    assert facts["first_season"] == 1999
    assert facts["source"] == "formula1.com"


def test_the_lap_record_comes_with_who_set_it_and_when():
    """A record without its holder is a number nobody can check."""
    facts = parse_circuit_facts(PAGE)

    assert facts["lap_record_time"] == "1:34.080"
    assert facts["lap_record_driver"] == "Sebastian Vettel"
    assert facts["lap_record_season"] == 2017


def test_numbers_arrive_as_numbers():
    """They are strings in the payload. Left that way, a length sorts
    lexically and a lap count concatenates."""
    facts = parse_circuit_facts(PAGE)

    assert isinstance(facts["length_km"], float)
    assert isinstance(facts["scheduled_laps"], int)
    assert isinstance(facts["first_season"], int)


def test_a_page_without_the_figures_yields_nothing():
    """What a redirect or an error page looks like. A dict of nulls would read
    like a circuit with no length."""
    assert parse_circuit_facts("<html><body>Not found</body></html>") == {}


def test_a_partial_page_is_not_half_believed():
    """The short name is the join key and the length is the headline figure;
    without both there is no usable record here."""
    assert parse_circuit_facts(r'{\"circuitShortName\":\"Monza\"}') == {}


def test_no_image_is_ever_collected():
    """The layouts on those pages are artwork, and this project draws its own
    from telemetry. Pinned so a later 'while we are here' cannot quietly add
    one."""
    facts = parse_circuit_facts(PAGE)

    assert not any(
        isinstance(value, str) and (".webp" in value or ".png" in value or ".jpg" in value)
        for value in facts.values()
    )
    assert not any("image" in key or "map" in key for key in facts)


# ── Finding the circuits ─────────────────────────────────────────────────────


INDEX = """
<a href="/en/racing/2026/pre-season-testing-1">testing</a>
<a href="/en/racing/2026/azerbaijan">Baku</a>
<a href="/en/racing/2026/bahrain">Bahrain</a>
<a href="/en/racing/2026/azerbaijan/circuit">Baku again</a>
"""


def test_testing_events_are_not_circuits_to_record():
    assert "pre-season-testing-1" not in season_slugs(INDEX)


def test_each_race_is_listed_once():
    """The page links every event several times over."""
    assert season_slugs(INDEX) == ["azerbaijan", "bahrain"]
