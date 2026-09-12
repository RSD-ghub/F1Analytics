"""jolpica, the third qualifying source.

It exists for a failure the other two share. The FIA path builds a URL from a
slug rule that is known to change — the 2024 documents use a different scheme,
which is why the rule resolves for 2025 and 2026 and 404s for 2024 — and the
grid and qualifying documents both depend on it, so one change takes both down
together. jolpica is a different host, a different format and different
maintainers.

It is slower than the FIA (it had nothing for Spain three hours after the
session, when the FIA document was already published), so it is tried last. It
is insurance against the FIA path breaking, not against it being slow.
"""

import pytest

from app.services.jolpica import JolpicaUnavailable, _parse, _seconds

# Shape taken from a real response: /ergast/f1/2025/14/qualifying.json
def _row(position, number, given, family, team, q1=None, q2=None, q3=None):
    row = {
        "position": str(position),
        "number": str(number),
        "Driver": {"givenName": given, "familyName": family,
                   "permanentNumber": str(number), "code": family[:3].upper()},
        "Constructor": {"name": team},
    }
    for key, value in (("Q1", q1), ("Q2", q2), ("Q3", q3)):
        if value:
            row[key] = value
    return row


def _field(n=20):
    return [
        _row(i + 1, i + 1, "Driver", "Name{}".format(i), "Team",
             q1="1:15.{:03d}".format(500 + i))
        for i in range(n)
    ]


# ── Lap times ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1:15.582", 75.582),
        ("0:59.100", 59.100),
        ("59.100", 59.100),
        ("1:31.824", 91.824),
    ],
)
def test_lap_times_parse_to_seconds(raw, expected):
    assert _seconds(raw) == pytest.approx(expected)


def test_a_missing_segment_is_no_time_not_an_error():
    """A driver knocked out in Q1 genuinely has no Q2, and the schema already
    reads 0.0 as "no time set"."""
    assert _seconds(None) == 0.0
    assert _seconds("") == 0.0


def test_an_unparseable_time_does_not_raise():
    assert _seconds("nonsense") == 0.0


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_a_full_field_parses():
    entries = _parse(_field())
    assert len(entries) == 20
    assert entries[0].position == 1
    assert entries[0].car_number == 1
    assert entries[0].driver_name == "Driver Name0"


def test_the_car_number_is_carried():
    """The whole reason this source is usable as a fallback: it supplies the
    same join key as the other two."""
    entries = _parse(_field())
    assert {e.car_number for e in entries} == set(range(1, 21))


def test_a_row_with_no_number_is_skipped_not_guessed():
    rows = _field()
    rows[5]["number"] = None
    rows[5]["Driver"].pop("permanentNumber")
    entries = _parse(rows)
    assert len(entries) == 19


def test_a_short_field_is_refused():
    with pytest.raises(JolpicaUnavailable):
        _parse(_field(4))


def test_duplicate_car_numbers_are_refused():
    rows = _field()
    rows[1]["number"] = rows[0]["number"]
    with pytest.raises(JolpicaUnavailable):
        _parse(rows)


# ── Availability ─────────────────────────────────────────────────────────────


async def test_an_unpublished_round_is_unavailable_not_broken(monkeypatch):
    """The normal state for a session that has just run."""
    import httpx

    from app.services import jolpica

    class _Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"MRData": {"RaceTable": {"Races": []}}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    with pytest.raises(JolpicaUnavailable) as exc:
        await jolpica.fetch_qualifying(2026, 14)
    assert "no qualifying classification" in str(exc.value)


# ── The chain ────────────────────────────────────────────────────────────────


def _runner_with(entry_list):
    from app.services.ingest_runner import IngestRunner

    runner = IngestRunner.__new__(IngestRunner)

    class _Source:
        def load_qualifying_entry_list(self, expected):
            return entry_list

    runner._source = _Source()
    return runner


def _expected():
    from app.models.schemas import ExpectedSession

    return ExpectedSession(season=2026, round=14, race_name="Spanish Grand Prix")


def _identities(n=20):
    return {i + 1: ("Driver Name{}".format(i), "Team") for i in range(n)}


async def test_jolpica_covers_the_fia_path_breaking(monkeypatch):
    """The failure this source exists for.

    The FIA URL is built from a slug rule that has changed before, and both the
    grid and qualifying documents depend on it — so a scheme change takes the
    whole FIA path down at once.
    """
    from app.services import fia_documents, jolpica as jolpica_module

    async def fia_down(*a, **k):
        raise fia_documents.GridDocumentUnavailable("404 — slug scheme changed")

    async def jolpica_up(season, round_number, **k):
        return _parse(_field())

    monkeypatch.setattr(fia_documents, "fetch_qualifying_classification", fia_down)
    monkeypatch.setattr(jolpica_module, "fetch_qualifying", jolpica_up)

    rows = await _runner_with(_identities())._qualifying_from_fallbacks(_expected())

    assert len(rows) == 20
    # Identity still came from the entry list, not from jolpica.
    assert rows[0].driver == "Driver Name0"


async def test_the_fia_is_preferred_when_both_have_it(monkeypatch):
    """Order of attempts is publication speed, not trust: the FIA is both."""
    from app.services import fia_documents, jolpica as jolpica_module

    called = []

    async def fia_up(*a, **k):
        called.append("fia")
        from app.services.fia_documents import QualifyingDocument, QualifyingEntry

        return QualifyingDocument(
            season=2026, round=14, event_name="Spanish Grand Prix",
            kind="provisional", url="x",
            entries=tuple(
                QualifyingEntry(position=i + 1, car_number=i + 1,
                                driver_name="X", team="Y")
                for i in range(20)
            ),
            document_number=44,
        )

    async def jolpica_up(*a, **k):
        called.append("jolpica")
        return _parse(_field())

    monkeypatch.setattr(fia_documents, "fetch_qualifying_classification", fia_up)
    monkeypatch.setattr(jolpica_module, "fetch_qualifying", jolpica_up)

    await _runner_with(_identities())._qualifying_from_fallbacks(_expected())
    assert called == ["fia"]


async def test_all_three_down_fails_loudly_with_every_reason(monkeypatch):
    """A silent empty result here would look identical to "no session"."""
    from app.services import fia_documents, jolpica as jolpica_module
    from app.services.fastf1_source import SessionFetchError

    async def fia_down(*a, **k):
        raise fia_documents.GridDocumentUnavailable("not published")

    async def jolpica_down(*a, **k):
        raise JolpicaUnavailable("nothing yet")

    monkeypatch.setattr(fia_documents, "fetch_qualifying_classification", fia_down)
    monkeypatch.setattr(jolpica_module, "fetch_qualifying", jolpica_down)

    with pytest.raises(SessionFetchError) as exc:
        await _runner_with(_identities())._qualifying_from_fallbacks(_expected())
    assert "FIA" in str(exc.value) and "jolpica" in str(exc.value)


async def test_no_entry_list_refuses_rather_than_inventing_identities():
    """Both fallbacks supply order only. Without the entry list there is nobody
    to attribute it to, and guessing names would create drivers with no history
    that then poison every future feature join."""
    from app.services.fastf1_source import SessionFetchError

    with pytest.raises(SessionFetchError) as exc:
        await _runner_with({})._qualifying_from_fallbacks(_expected())
    assert "entry list" in str(exc.value)
