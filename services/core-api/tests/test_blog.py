"""One Blog tests.

The blog sits next to calibrated probabilities on the same page, so its failure
mode is not "looks wrong" — it is "reads authoritative and is invented". These
tests pin the properties that stop that: every entry traces to sources, the
facts are computed rather than narrated, and the absence of an LLM degrades the
page instead of breaking it.
"""

import pytest

from app.models.blog import BlogEntry, EntryKind
from app.services import blog as builder
from app.services.bernie import Bernie, BernieUnavailable


def _practice(times, laps=10, session="FP2"):
    return [
        {"driver": d, "session_name": session, "long_run_seconds": t,
         "long_run_laps": laps, "best_lap_seconds": b}
        for d, t, b in times
    ]


#: Mirrors the real 2024 Bahrain shape: the driver who tops the timesheet on a
#: single low-fuel lap (Echo) is slowest over a stint, while the quickest on
#: race runs (Alpha) is only mid-pack on one lap.
FIELD = _practice([
    ("Alpha", 95.0, 91.5),   # quickest over a stint, P4 on one lap
    ("Bravo", 95.4, 91.0),
    ("Charlie", 95.9, 91.9),
    ("Delta", 96.4, 92.4),
    ("Echo", 96.9, 89.9),    # fastest single lap, slowest over a stint
])


# ── Practice entry ───────────────────────────────────────────────────────────


def test_practice_entry_ranks_by_long_run_not_headline_lap():
    entry = builder.practice_entry(2026, 5, "Test GP", FIELD)

    assert "Alpha" in entry.headline
    assert entry.table[0]["driver"] == "Alpha"
    assert entry.table[0]["gap_pct"] == 0.0


def test_a_flattering_headline_lap_is_called_out():
    """The most useful sentence in a practice report is usually this one."""
    entry = builder.practice_entry(2026, 5, "Test GP", FIELD)
    labels = {f.label: f for f in entry.facts}

    assert "Fastest lap flattered" in labels
    assert labels["Fastest lap flattered"].value == "Echo"


def test_a_thin_session_produces_no_entry():
    """Two drivers is not a practice report; better nothing than a fake one."""
    assert builder.practice_entry(2026, 5, "T", _practice([("A", 95.0, 90.0)])) is None


def test_runs_without_laps_are_excluded():
    rows = [
        {"driver": "A", "session_name": "FP2", "long_run_seconds": 95.0,
         "long_run_laps": 0, "best_lap_seconds": 90.0}
    ] * 5
    assert builder.practice_entry(2026, 5, "T", rows) is None


def test_practice_entry_records_its_sources():
    entry = builder.practice_entry(2026, 5, "Test GP", FIELD)

    assert entry.is_sourced
    assert "practice_pace" in entry.sources[0]
    assert "FP2" in entry.sources[0]


# ── Forecast entry ───────────────────────────────────────────────────────────


def _prediction(window="pre_quali", published=("podium", "points"), quality=None):
    return {
        "prediction_id": "p1", "season": 2026, "round": 5, "window": window,
        "model_version": "race-plackett-luce-v4",
        "published_markets": list(published),
        "data_quality": quality or {"complete": True},
        "driver_probabilities": [
            {"driver": "Alpha", "team": "A", "p_win": 0.4 if "win" in published else None,
             "p_podium": 0.85, "p_points": 0.99},
            {"driver": "Bravo", "team": "B", "p_win": 0.3 if "win" in published else None,
             "p_podium": 0.70, "p_points": 0.95},
        ],
    }


def test_pre_quali_states_plainly_that_no_winner_is_called():
    """Declining to predict is a claim about our limits, not small print."""
    entry = builder.forecast_entry(_prediction())
    labels = {f.label: f for f in entry.facts}

    assert "No winner call" in labels
    assert "no better than guessing" in labels["No winner call"].detail


def test_post_quali_names_a_favourite():
    entry = builder.forecast_entry(
        _prediction(window="post_quali", published=("win", "podium", "points"))
    )
    labels = {f.label: f for f in entry.facts}

    assert "Most likely winner" in labels
    assert labels["Most likely winner"].value == "Alpha"


def test_a_provisional_grid_is_surfaced_to_the_reader():
    entry = builder.forecast_entry(
        _prediction(
            window="post_quali", published=("win", "podium", "points"),
            quality={"complete": True, "grid_is_provisional": True},
        )
    )
    assert any(f.label == "Grid caveat" for f in entry.facts)


def test_incomplete_data_is_surfaced_to_the_reader():
    entry = builder.forecast_entry(
        _prediction(quality={"complete": False, "notes": "2 prior rounds missing"})
    )
    caveat = next(f for f in entry.facts if f.label == "Data caveat")

    assert "2 prior rounds missing" in caveat.detail


def test_an_empty_forecast_produces_no_entry():
    payload = _prediction()
    payload["driver_probabilities"] = []
    assert builder.forecast_entry(payload) is None


# ── Assembly ─────────────────────────────────────────────────────────────────


def test_unsourced_entries_are_never_published():
    """An entry with no provenance is indistinguishable from an invented one."""
    orphan = BlogEntry(
        entry_id="x", season=2026, round=5, kind=EntryKind.RESULT,
        headline="Something happened", sources=[],
    )
    page = builder.assemble(2026, 5, "T", "C", [orphan], narration_available=False)

    assert page.entries == []


def test_entries_are_ordered_as_the_weekend_unfolded():
    practice = builder.practice_entry(2026, 5, "T", FIELD)
    forecast = builder.forecast_entry(_prediction())
    page = builder.assemble(2026, 5, "T", "C", [forecast, practice], False)

    assert [e.kind for e in page.entries] == [EntryKind.PRACTICE, EntryKind.FORECAST]


def test_missing_entries_are_skipped_not_fatal():
    """A downstream outage costs one panel, not the page."""
    page = builder.assemble(
        2026, 5, "T", "C",
        [None, builder.practice_entry(2026, 5, "T", FIELD), None], False,
    )
    assert len(page.entries) == 1


# ── Narration ────────────────────────────────────────────────────────────────


class SilentLLM:
    available = False

    async def complete(self, *a, **k):
        return ""


class TalkativeLLM:
    available = True

    def __init__(self):
        self.saw = None

    async def complete_verbose(self, prompt, system=None, max_tokens=1024,
                               temperature=0.2, history=None, reasoning_effort=None):
        from f1_common.llm import Completion
        self.saw = prompt
        return Completion(text="The long runs told the real story.")

    async def complete(self, prompt, system=None, max_tokens=1024, temperature=0.2):
        self.saw = prompt
        return "The long runs told the real story."


async def test_no_llm_leaves_the_facts_intact():
    """The page must be useful with narration switched off entirely."""
    entry = builder.practice_entry(2026, 5, "T", FIELD)
    narrated = await builder.narrate(Bernie(SilentLLM()), entry)

    assert narrated.narrative is None
    assert narrated.facts
    assert narrated.table


async def test_narration_is_given_only_the_entrys_own_facts():
    """Bernie may rephrase; he may not introduce. The prompt is the guarantee."""
    llm = TalkativeLLM()
    entry = builder.practice_entry(2026, 5, "T", FIELD)
    narrated = await builder.narrate(Bernie(llm), entry)

    assert narrated.narrative == "The long runs told the real story."
    assert "Alpha" in llm.saw
    # Nothing beyond the computed facts reaches the model.
    assert "only information you may use" in llm.saw.lower()


async def test_a_failing_llm_does_not_break_the_entry():
    class Broken:
        available = True

        async def complete(self, *a, **k):
            raise RuntimeError("upstream down")

    entry = builder.practice_entry(2026, 5, "T", FIELD)
    narrated = await builder.narrate(Bernie(Broken()), entry)

    assert narrated.narrative is None
    assert narrated.headline


def test_a_forecast_without_a_confirmed_grid_does_not_claim_the_grid_was_set():
    """Caught on a live weekend.

    The 2026 Italian GP forecast locked at 07:27 on qualifying classification,
    while the same page's qualifying section reported that Antonelli qualified
    P7 and starts P19. The forecast still announced itself as "locked with the
    grid set" — a claim the rest of the page contradicted.
    """
    entry = builder.forecast_entry(
        {
            "season": 2026, "round": 13, "window": "post_quali",
            "published_markets": ["win", "podium", "points"],
            "data_quality": {"grid_is_provisional": True, "complete": False},
            "driver_probabilities": [
                {"driver": "Kimi Antonelli", "p_win": 0.28, "p_podium": 0.67, "p_points": 0.9},
            ],
        },
        race_name="Italian Grand Prix",
    )

    assert "with the grid set" not in entry.headline
    assert "before the penalties were confirmed" in entry.headline


def test_a_forecast_on_the_official_grid_does_say_the_grid_was_set():
    entry = builder.forecast_entry(
        {
            "season": 2026, "round": 13, "window": "post_quali",
            "published_markets": ["win", "podium", "points"],
            "data_quality": {"grid_is_provisional": False,
                             "grid_source": "official_final", "complete": True},
            "driver_probabilities": [
                {"driver": "George Russell", "p_win": 0.31, "p_podium": 0.75, "p_points": 0.95},
            ],
        },
        race_name="Italian Grand Prix",
    )

    assert entry.headline.endswith("with the grid set")
