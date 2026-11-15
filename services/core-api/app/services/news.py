"""How paddock news is ordered on the way out.

The store hands back headlines newest first, which is the honest order and the
wrong presentation. Formula1.com's feed carries no publication date on any
item, so every one of its stories is stamped with the moment we first read it
— which is to say, always just now. Left alone, ten Formula1.com tiles sit at
the top of the page and Autosport's photographs start below the fold.

Ranking undated stories below dated ones was the obvious fix and the wrong
one: Autosport's dated items accumulate across the whole retention window, so
the rule would push Formula1.com off the page entirely rather than down it.

So this is a presentation rule and nothing more. No outlet may fill more than
two cards in a row. Nothing is dropped, nothing is hidden, no timestamp is
altered, and within one outlet the newest story still comes first — a reader
who sorts by date sees exactly what the store holds.

The page that gives its first card double width leads with the newest story
that has a photograph, for the same reason and with the same restraint.
"""

from typing import Any, List, Sequence

#: Two is a pair, which reads as a column on a card grid. Three reads as a
#: single outlet owning the page.
MAX_CONSECUTIVE_FROM_ONE_OUTLET = 2


def _outlet(item: Any) -> str:
    """The outlet an item came from, however the item is shaped."""
    if isinstance(item, dict):
        return str(item.get("source") or item.get("outlet") or "")
    return str(getattr(item, "source", "") or getattr(item, "outlet", "") or "")


def _picture(item: Any) -> str:
    """The feed's own picture for an item, or "" when it sent none."""
    if isinstance(item, dict):
        return str(item.get("image_url") or "")
    return str(getattr(item, "image_url", "") or "")


def _spread(
    waiting: List[Any],
    ordered: List[Any],
    run_outlet: str,
    run_length: int,
) -> List[Any]:
    """The greedy pass, continuing from a run already begun.

    Take the next story waiting, unless it would be the third in a row from
    its outlet and some other outlet still has one waiting, in which case take
    that other outlet's next story first. When no other outlet has anything
    left, the run simply continues — a page of one outlet is better than a
    short page.
    """
    while waiting:
        index = 0
        if run_length >= MAX_CONSECUTIVE_FROM_ONE_OUTLET and _outlet(waiting[0]) == run_outlet:
            index = next(
                (i for i, item in enumerate(waiting) if _outlet(item) != run_outlet),
                0,
            )
        chosen = waiting.pop(index)
        outlet = _outlet(chosen)
        run_length = run_length + 1 if outlet == run_outlet else 1
        run_outlet = outlet
        ordered.append(chosen)
    return ordered


def spread_outlets(items: Sequence[Any]) -> List[Any]:
    """Return ``items`` reordered so no outlet fills more than two in a row."""
    return _spread(list(items), [], "", 0)


def lead_with_picture(items: Sequence[Any]) -> List[Any]:
    """``spread_outlets``, but the newest story with a photograph goes first.

    For the views that give the first card the width of two. A card with no
    picture is a tile naming the outlet, which is a reasonable card and a poor
    lead; the outlet that dates nothing is also the one that sends no
    pictures, so the lead would otherwise always be a grey tile. Nothing is
    dropped and no story moves down more than a place or two.
    """
    waiting = list(items)
    index = next((i for i, item in enumerate(waiting) if _picture(item)), None)
    if index is None:
        return _spread(waiting, [], "", 0)
    lead = waiting.pop(index)
    return _spread(waiting, [lead], _outlet(lead), 1)
