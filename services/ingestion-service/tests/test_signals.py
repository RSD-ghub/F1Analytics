"""Signal extraction tests.

The rule-based path is tested against real race-control phrasing. The LLM path is
tested almost entirely through its failure modes — that is where the risk lives,
because every one of them must degrade to "no signals" rather than to an
exception that fails an ingest.
"""

import pytest

from app.models.schemas import RaceControlRow
from app.services import signals


def _message(text: str, lap: int = 0) -> RaceControlRow:
    return RaceControlRow(
        id="m-{}".format(abs(hash(text))), season=2024, round=5, message=text, lap=lap
    )


# ── Rule-based ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CAR 44 (HAM) 5 SECOND TIME PENALTY - TRACK LIMITS", "penalty"),
        ("CAR 1 (VER) DRIVE THROUGH PENALTY", "penalty"),
        ("CAR 16 (LEC) 10 SECOND STOP AND GO PENALTY", "penalty"),
        ("CAR 4 (NOR) UNDER INVESTIGATION - CAUSING A COLLISION", "investigation"),
        ("RED FLAG", "red_flag"),
        ("VIRTUAL SAFETY CAR DEPLOYED", "safety_car"),
        ("SAFETY CAR IN THIS LAP", "safety_car"),
        ("RAIN EXPECTED IN 10 MINUTES", "weather"),
        ("TRACK LIMITS AT TURN 4 LAP 12", "track_limits"),
    ],
)
def test_real_race_control_phrasing_is_categorised(text, expected):
    result = signals.extract_rule_based(2024, 5, [_message(text)])
    assert len(result) == 1
    assert result[0].category == expected


def test_penalty_wins_over_the_less_specific_match():
    """Ordering matters: this mentions track limits but *is* a penalty."""
    result = signals.extract_rule_based(
        2024, 5, [_message("CAR 44 (HAM) 5 SECOND TIME PENALTY - TRACK LIMITS")]
    )
    assert result[0].category == "penalty"


def test_driver_code_is_pulled_from_the_car_reference():
    result = signals.extract_rule_based(
        2024, 5, [_message("CAR 44 (HAM) 5 SECOND TIME PENALTY")]
    )
    assert result[0].driver == "HAM"


def test_message_without_a_driver_leaves_the_field_empty():
    result = signals.extract_rule_based(2024, 5, [_message("RED FLAG")])
    assert result[0].driver == ""


def test_uninteresting_messages_produce_nothing():
    result = signals.extract_rule_based(
        2024, 5, [_message("GREEN LIGHT - PIT EXIT OPEN"), _message("   ")]
    )
    assert result == []


def test_signals_record_the_round_they_describe():
    """The point-in-time boundary: this must never feed a round-5 forecast."""
    result = signals.extract_rule_based(2024, 5, [_message("RED FLAG", lap=12)])

    assert result[0].describes_round == 5
    assert result[0].lap == 12
    assert result[0].source == "rule"


def test_summarise_counts_by_category():
    result = signals.extract_rule_based(
        2024,
        5,
        [
            _message("CAR 44 (HAM) 5 SECOND TIME PENALTY"),
            _message("CAR 1 (VER) 5 SECOND TIME PENALTY"),
            _message("RED FLAG"),
        ],
    )
    assert signals.summarise(result) == {"penalty": 2, "red_flag": 1}


# ── LLM path: every failure must be survivable ───────────────────────────────


class FakeLLM:
    def __init__(self, available=True, payload=None, raises=None):
        self._available = available
        self._payload = payload
        self._raises = raises
        self.calls = 0

    @property
    def available(self):
        return self._available

    async def complete_json(self, prompt, system=None, max_tokens=1024):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._payload

    async def complete(self, prompt, system=None, max_tokens=1024, temperature=0.2):
        return ""


async def test_unconfigured_provider_is_skipped_not_failed():
    client = FakeLLM(available=False)
    result = await signals.extract_with_llm(client, 2024, 5, [_message("RED FLAG")])

    assert result == []
    assert client.calls == 0  # no pointless network attempt


async def test_network_failure_degrades_to_no_signals():
    client = FakeLLM(raises=RuntimeError("connection refused"))
    result = await signals.extract_with_llm(client, 2024, 5, [_message("RED FLAG")])
    assert result == []


async def test_malformed_json_degrades_to_no_signals():
    client = FakeLLM(payload=None)  # complete_json returns None on unparseable output
    result = await signals.extract_with_llm(client, 2024, 5, [_message("RED FLAG")])
    assert result == []


async def test_wrong_shape_degrades_to_no_signals():
    client = FakeLLM(payload={"category": "penalty"})  # object, not array
    result = await signals.extract_with_llm(client, 2024, 5, [_message("RED FLAG")])
    assert result == []


async def test_one_bad_element_does_not_discard_the_rest():
    client = FakeLLM(
        payload=[
            {"category": "penalty", "driver": "HAM", "detail": "5s penalty", "lap": 12},
            "not an object",
            {"category": "safety_car", "detail": ""},  # no detail, skipped
            {"category": "red_flag", "detail": "Session stopped", "lap": "bad"},
        ]
    )
    result = await signals.extract_with_llm(client, 2024, 5, [_message("x")])

    assert [s.category for s in result] == ["penalty", "red_flag"]
    assert result[0].lap == 12
    assert result[1].lap == 0  # unparseable lap coerced, element kept
    assert all(s.source == "llm" for s in result)


async def test_empty_message_list_makes_no_call():
    client = FakeLLM(payload=[])
    assert await signals.extract_with_llm(client, 2024, 5, []) == []
    assert client.calls == 0


async def test_llm_signals_carry_the_point_in_time_boundary():
    client = FakeLLM(payload=[{"category": "penalty", "detail": "5s for Hamilton"}])
    result = await signals.extract_with_llm(client, 2024, 5, [_message("x")])

    assert result[0].describes_round == 5
    assert result[0].season == 2024
