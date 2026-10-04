"""An event held somewhere other than the country it is named after.

The FIA's document URLs are built from the event name, and the rule resolved
all 24 events of 2025. It does not resolve a relocated race: the 2026 Bahrain
Grand Prix ran at Sepang and the FIA published its grid under "Bahrain Grand
Prix in Malaysia".

The cost was not cosmetic. Every fetch 404'd across the weekend, no confirmed
grid was ever stored, and the final-grid lock window correctly refused to fire
— so the race was forecast on the qualifying classification while fourteen of
twenty-two cars started somewhere else.
"""

from app.services.fia_documents import document_url, names_to_try


BAHRAIN_IN_MALAYSIA = "FORMULA 1 GULF AIR BAHRAIN GRAND PRIX IN MALAYSIA 2026"


def test_the_schedules_name_is_always_tried_first():
    """It resolves almost every event, and a second request is only worth
    making when the first has missed."""
    assert names_to_try("Monaco Grand Prix", "")[0] == "Monaco Grand Prix"


def test_a_relocated_race_gains_the_name_the_fia_published_it_under():
    names = names_to_try("Bahrain Grand Prix", BAHRAIN_IN_MALAYSIA)

    assert "bahrain grand prix in malaysia" in names
    assert document_url(2026, names[1]).endswith(
        "2026_bahrain_grand_prix_in_malaysia_-_final_starting_grid.pdf"
    )


def test_the_sponsor_and_the_series_are_dropped_without_listing_them():
    """"FORMULA 1" and "GULF AIR" sit before the schedule's name in the title,
    so cutting from that name forward removes them without a list of sponsors
    to maintain."""
    names = names_to_try("Bahrain Grand Prix", BAHRAIN_IN_MALAYSIA)

    assert "formula" not in names[1]
    assert "gulf" not in names[1]


def test_the_year_is_dropped():
    """It is already the first component of the URL."""
    assert not names_to_try("Bahrain Grand Prix", BAHRAIN_IN_MALAYSIA)[1].endswith("2026")


def test_an_event_whose_official_title_is_in_another_language_gains_nothing():
    """"GRAN PREMIO D'ITALIA" does not contain "Italian Grand Prix", and
    nothing can be cut from it safely. The plain slug resolves those anyway."""
    names = names_to_try("Italian Grand Prix", "FORMULA 1 PIRELLI GRAN PREMIO D'ITALIA 2026")

    assert names == ["Italian Grand Prix"]


def test_a_title_that_only_adds_a_sponsor_produces_no_second_attempt():
    """Otherwise every event would cost two requests to discover the first one
    worked."""
    names = names_to_try("Azerbaijan Grand Prix", "FORMULA 1 AZERBAIJAN GRAND PRIX 2026")

    assert names == ["Azerbaijan Grand Prix"]


def test_no_official_title_is_survivable():
    """We may not hold one — it is fetched for the circuit panel, on its own
    schedule, and a grid must not wait for it."""
    assert names_to_try("Bahrain Grand Prix", "") == ["Bahrain Grand Prix"]
