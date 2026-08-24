"""Building One Blog entries from the structured record.

**Facts first, prose second.** Every number in an entry is computed here from
data the system already holds; Bernie is handed those numbers and may only
rephrase them. That ordering is the whole safety property — a forecasting
product that publishes calibrated probabilities cannot also publish a language
model's guesses about what happened in practice.

Entries are therefore useful with no LLM at all. Narration is an enhancement.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

from app.models.blog import BlogEntry, BlogFact, EntryKind, WeekendBlog
from app.services.bernie import Bernie, BernieUnavailable

logger = logging.getLogger(__name__)

TOP_N = 6


def _entry_id(season: int, round_number: int, kind: EntryKind) -> str:
    return "{}-{}-{}".format(season, round_number, kind.value)


# ── Practice ─────────────────────────────────────────────────────────────────


def practice_entry(
    season: int,
    round_number: int,
    race_name: str,
    practice_rows: Sequence[Dict[str, Any]],
) -> Optional[BlogEntry]:
    """What the long runs said.

    Long-run pace, not the headline lap. A single fast lap is set on low fuel in
    a favourable engine mode and teams vary both deliberately; a median over a
    sustained stint is much closer to what the car will do on Sunday. The gap
    between those two orderings is usually the most interesting thing in the
    session, so it is called out explicitly.
    """
    usable = [
        row for row in practice_rows
        if (row.get("long_run_seconds") or 0) > 0 and (row.get("long_run_laps") or 0) > 0
    ]
    if len(usable) < 3:
        return None

    session_name = usable[0].get("session_name", "practice")
    by_long_run = sorted(usable, key=lambda r: r["long_run_seconds"])
    by_best = sorted(
        [r for r in usable if (r.get("best_lap_seconds") or 0) > 0],
        key=lambda r: r["best_lap_seconds"],
    )
    reference = by_long_run[0]["long_run_seconds"]

    long_rank = {r["driver"]: i + 1 for i, r in enumerate(by_long_run)}
    best_rank = {r["driver"]: i + 1 for i, r in enumerate(by_best)}

    facts = [
        BlogFact(
            label="Quickest over a stint",
            value=by_long_run[0]["driver"],
            detail="{:.3f}s median over {} laps".format(
                reference, by_long_run[0]["long_run_laps"]
            ),
        )
    ]
    if len(by_long_run) > 1:
        facts.append(
            BlogFact(
                label="Next closest",
                value=by_long_run[1]["driver"],
                detail="{:+.2f}s a lap".format(
                    by_long_run[1]["long_run_seconds"] - reference
                ),
            )
        )

    # A headline time that flatters: fastest on one lap, mid-pack over a stint.
    if by_best:
        headline_setter = by_best[0]["driver"]
        stint_place = long_rank.get(headline_setter)
        if stint_place and stint_place > 3:
            facts.append(
                BlogFact(
                    label="Fastest lap flattered",
                    value=headline_setter,
                    detail="topped the timesheet but P{} on race runs".format(
                        stint_place
                    ),
                )
            )

    # Biggest climber from single-lap order to race-run order.
    movers = [
        (best_rank[r["driver"]] - long_rank[r["driver"]], r["driver"])
        for r in usable
        if r["driver"] in best_rank and r["driver"] in long_rank
    ]
    if movers:
        jump, driver = max(movers)
        if jump >= 4:
            facts.append(
                BlogFact(
                    label="Better than it looked",
                    value=driver,
                    detail="P{} on one lap, P{} over a stint".format(
                        best_rank[driver], long_rank[driver]
                    ),
                )
            )

    table = [
        {
            "driver": row["driver"],
            "long_run_seconds": round(row["long_run_seconds"], 3),
            "gap_pct": round(100 * (row["long_run_seconds"] - reference) / reference, 2),
            "laps": row["long_run_laps"],
        }
        for row in by_long_run[:TOP_N]
    ]

    return BlogEntry(
        entry_id=_entry_id(season, round_number, EntryKind.PRACTICE),
        season=season,
        round=round_number,
        race_name=race_name,
        kind=EntryKind.PRACTICE,
        headline="{} set the pace on race runs".format(by_long_run[0]["driver"]),
        summary=(
            "Long-run pace from {}, measured as the median of sustained stints "
            "rather than a single low-fuel lap.".format(session_name)
        ),
        facts=facts,
        table=table,
        sources=[
            "practice_pace: {} drivers, {} session, {} timed long-run laps".format(
                len(usable), session_name,
                sum(r.get("long_run_laps", 0) for r in usable),
            )
        ],
    )


# ── Qualifying ───────────────────────────────────────────────────────────────


def qualifying_entry(
    season: int,
    round_number: int,
    race_name: str,
    quali_rows: Sequence[Dict[str, Any]],
) -> Optional[BlogEntry]:
    """What qualifying settled, in time rather than in ordinal.

    "P3" hides the thing that matters. Third by five hundredths and third by
    nine tenths describe completely different Sundays, so the gap to pole is
    reported as a time and the field's spread is called out.

    Grid penalties are surfaced separately: a driver who qualifies second and
    starts twelfth is the single most important fact on the page, and the
    classification alone conceals it.
    """
    timed = []
    for row in quali_rows:
        times = [row.get(k) or 0.0 for k in ("q1_seconds", "q2_seconds", "q3_seconds")]
        best = min([t for t in times if t > 0], default=0.0)
        if best > 0 and 1 <= (row.get("position") or 999) <= 30:
            timed.append((row, best))
    if len(timed) < 3:
        return None

    timed.sort(key=lambda pair: pair[0]["position"])
    pole_row, pole_time = min(timed, key=lambda pair: pair[1])

    facts = [
        BlogFact(
            label="Pole",
            value=pole_row.get("driver", "?"),
            detail="{:.3f}s".format(pole_time),
        )
    ]
    if len(timed) > 1:
        runner_up, runner_time = timed[1]
        facts.append(
            BlogFact(
                label="Margin",
                value="{:+.3f}s".format(runner_time - pole_time),
                detail="to {}".format(runner_up.get("driver", "?")),
            )
        )

    # Penalties: qualifying classification vs the confirmed starting grid.
    penalised = [
        (row, row["grid_position"] - row["position"])
        for row, _ in timed
        if (row.get("grid_position") or 0) > 0
        and row["grid_position"] != row["position"]
    ]
    for row, drop in sorted(penalised, key=lambda pair: -abs(pair[1]))[:3]:
        facts.append(
            BlogFact(
                label="Grid penalty",
                value=row.get("driver", "?"),
                detail="qualified P{}, starts P{}".format(
                    row["position"], row["grid_position"]
                ),
            )
        )
    if not penalised:
        facts.append(
            BlogFact(
                label="Grid",
                value="As qualified",
                detail="No grid penalties applied.",
            )
        )

    table = [
        {
            "position": row.get("position"),
            "driver": row.get("driver"),
            "team": row.get("team"),
            "gap_to_pole": round(best - pole_time, 3),
            "starts": row.get("grid_position") or row.get("position"),
        }
        for row, best in timed[:TOP_N]
    ]

    return BlogEntry(
        entry_id=_entry_id(season, round_number, EntryKind.QUALIFYING),
        season=season,
        round=round_number,
        race_name=race_name,
        kind=EntryKind.QUALIFYING,
        headline="{} takes pole".format(pole_row.get("driver", "?")),
        summary=(
            "Gaps are shown as times rather than places — a tenth and a second "
            "look identical in the classification and are not."
        ),
        facts=facts,
        table=table,
        sources=["qualifying: {} classified drivers".format(len(timed))],
    )


# ── Result ───────────────────────────────────────────────────────────────────


def result_entry(
    season: int,
    round_number: int,
    race_name: str,
    results: Sequence[Dict[str, Any]],
    prediction: Optional[Dict[str, Any]] = None,
) -> Optional[BlogEntry]:
    """What happened, and how our forecast held up.

    The forecast comparison is included whether it flatters us or not. A track
    record that only appears when the model was right is not a track record.
    """
    def classified(row):
        code = str(row.get("classified_position") or "")
        return code.isdigit() if code else 1 <= (row.get("position") or 999) <= 30

    finishers = sorted(
        [r for r in results if classified(r)], key=lambda r: r["position"]
    )
    if not finishers:
        return None

    winner = finishers[0]
    facts = [
        BlogFact(label="Winner", value=winner.get("driver", "?"),
                 detail=winner.get("team", "")),
    ]
    podium = ", ".join(r.get("driver", "?") for r in finishers[:3])
    facts.append(BlogFact(label="Podium", value=podium))

    retirements = [r for r in results if not classified(r)]
    if retirements:
        facts.append(
            BlogFact(
                label="Retirements",
                value=str(len(retirements)),
                detail=", ".join(r.get("driver", "?") for r in retirements[:4]),
            )
        )

    sources = ["results: {} classified, {} retired".format(
        len(finishers), len(retirements))]

    if prediction:
        probabilities = {
            row.get("driver"): row for row in prediction.get("driver_probabilities") or []
        }
        actual_podium = {r.get("driver") for r in finishers[:3]}
        called = sorted(
            probabilities.values(), key=lambda r: -(r.get("p_podium") or 0.0)
        )[:3]
        hits = len({r.get("driver") for r in called} & actual_podium)
        facts.append(
            BlogFact(
                label="Our podium call",
                value="{} of 3 correct".format(hits),
                detail=", ".join(
                    "{} ({:.0%})".format(r.get("driver"), r.get("p_podium") or 0.0)
                    for r in called
                ),
            )
        )
        winner_probability = probabilities.get(winner.get("driver"), {})
        stated = winner_probability.get("p_win")
        if stated is not None:
            facts.append(
                BlogFact(
                    label="We gave the winner",
                    value="{:.0%}".format(stated),
                    detail=(
                        "A low number here is not a miss on its own — long shots "
                        "are supposed to win sometimes, which is what calibration "
                        "measures."
                    ),
                )
            )
        sources.append("prediction {}".format(prediction.get("prediction_id", "?")))

    return BlogEntry(
        entry_id=_entry_id(season, round_number, EntryKind.RESULT),
        season=season,
        round=round_number,
        race_name=race_name,
        kind=EntryKind.RESULT,
        headline="{} wins".format(winner.get("driver", "?")),
        summary="How the race finished, and how the locked forecast compared.",
        facts=facts,
        table=[
            {"position": r.get("position"), "driver": r.get("driver"),
             "team": r.get("team"), "points": r.get("points")}
            for r in finishers[:TOP_N]
        ],
        sources=sources,
    )


# ── Forecast ─────────────────────────────────────────────────────────────────


def forecast_entry(
    prediction: Dict[str, Any], race_name: str = ""
) -> Optional[BlogEntry]:
    """What we committed to, and what we deliberately did not claim."""
    probabilities = prediction.get("driver_probabilities") or []
    if not probabilities:
        return None

    published = prediction.get("published_markets") or []
    quality = prediction.get("data_quality") or {}
    window = prediction.get("window", "")
    ranked = sorted(probabilities, key=lambda r: -(r.get("p_podium") or 0.0))

    facts = [
        BlogFact(
            label="Most likely podium",
            value=ranked[0].get("driver", "?"),
            detail="{:.0%} chance of a top-three finish".format(
                ranked[0].get("p_podium") or 0.0
            ),
        )
    ]
    if "win" in published and ranked[0].get("p_win") is not None:
        best_win = max(probabilities, key=lambda r: r.get("p_win") or 0.0)
        facts.append(
            BlogFact(
                label="Most likely winner",
                value=best_win.get("driver", "?"),
                detail="{:.0%}".format(best_win.get("p_win") or 0.0),
            )
        )
    else:
        # Stated as a fact, not hidden in small print. Declining to predict is
        # a claim about our own limits and the reader is entitled to it.
        facts.append(
            BlogFact(
                label="No winner call",
                value="Not published before qualifying",
                detail=(
                    "Measured over two held-out seasons, this window's "
                    "win-market accuracy was no better than guessing, so we "
                    "make no win claim until the grid is set."
                ),
            )
        )

    if quality.get("grid_is_provisional"):
        facts.append(
            BlogFact(
                label="Grid caveat",
                value="Penalties not yet applied",
                detail="Positions are qualifying classification.",
            )
        )
    if not quality.get("complete", True):
        facts.append(
            BlogFact(
                label="Data caveat",
                value="Incomplete inputs",
                detail=quality.get("notes", ""),
            )
        )

    table = [
        {
            "driver": row.get("driver"),
            "team": row.get("team"),
            "p_win": row.get("p_win"),
            "p_podium": row.get("p_podium"),
            "p_points": row.get("p_points"),
        }
        for row in ranked[:TOP_N]
    ]

    label = "before qualifying" if window == "pre_quali" else "with the grid set"
    return BlogEntry(
        entry_id=_entry_id(
            prediction.get("season", 0), prediction.get("round", 0), EntryKind.FORECAST
        ),
        season=prediction.get("season", 0),
        round=prediction.get("round", 0),
        race_name=race_name or prediction.get("race_name", ""),
        kind=EntryKind.FORECAST,
        occurred_at=prediction.get("locked_at"),
        headline="Our forecast, locked {}".format(label),
        summary=(
            "Probabilities are locked and permanent. They are scored against "
            "the result afterwards, whatever they turn out to be."
        ),
        facts=facts,
        table=table,
        sources=[
            "prediction {} · model {}".format(
                prediction.get("prediction_id", "?"),
                prediction.get("model_version", "?"),
            )
        ],
    )


# ── Assembly ─────────────────────────────────────────────────────────────────


async def narrate(bernie: Bernie, entry: BlogEntry) -> BlogEntry:
    """Attach prose over the entry's own facts. Never adds information."""
    if not bernie.available:
        return entry
    facts = {fact.label: "{} — {}".format(fact.value, fact.detail).strip(" —")
             for fact in entry.facts}
    try:
        entry.narrative = await bernie.explain(
            question="Explain this to a race fan in two short paragraphs: {}".format(
                entry.headline
            ),
            facts=facts,
            max_tokens=400,
        )
    except BernieUnavailable as exc:
        logger.info("no narration for %s: %s", entry.entry_id, exc)
    return entry


def assemble(
    season: int,
    round_number: int,
    race_name: str,
    circuit: str,
    entries: Sequence[Optional[BlogEntry]],
    narration_available: bool,
) -> WeekendBlog:
    """Order the timeline and drop anything unsourced.

    An entry with no sources cannot be shown: the whole premise is that every
    claim traces back to stored data, and an unsourced entry is indistinguishable
    from an invented one.
    """
    ordered = [e for e in entries if e is not None and e.is_sourced]
    kind_order = {
        EntryKind.PRACTICE: 0,
        EntryKind.QUALIFYING: 1,
        EntryKind.FORECAST: 2,
        EntryKind.RESULT: 3,
    }
    ordered.sort(key=lambda e: kind_order.get(e.kind, 99))
    return WeekendBlog(
        season=season,
        round=round_number,
        race_name=race_name,
        circuit=circuit,
        entries=ordered,
        narration_available=narration_available,
    )
