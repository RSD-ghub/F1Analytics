"""LLM budgets and explanation caching.

These exist only because the app is getting a public URL. Locally the number of
callers is one; with a URL it is however many people find it, and every Bernie
call spends real money on someone else's API.
"""

import pytest

from app.services.usage import BudgetExceeded, UsageStore


class _Collection:
    def __init__(self):
        self.docs = {}

    async def find_one_and_update(self, query, update, upsert=False, return_document=True):
        key = (query.get("day"), query.get("subject"))
        current = self.docs.get(key, {"day": key[0], "subject": key[1], "calls": 0})
        current["calls"] += update["$inc"]["calls"]
        self.docs[key] = current
        return current

    async def find_one(self, query, projection=None):
        return self.docs.get(query.get("key"))

    async def update_one(self, query, update, upsert=False):
        self.docs[query["key"]] = update["$set"]

    async def create_index(self, *a, **k):
        pass


class _DB:
    def __init__(self):
        self._c = {}

    def __getitem__(self, name):
        return self._c.setdefault(name, _Collection())


# ── Per-user budget ──────────────────────────────────────────────────────────


async def test_a_user_can_spend_up_to_their_limit():
    store = UsageStore(_DB())
    for _ in range(3):
        await store.consume("alice", per_user=3, per_day=100)


async def test_the_next_call_over_the_limit_is_refused():
    store = UsageStore(_DB())
    for _ in range(3):
        await store.consume("alice", per_user=3, per_day=100)

    with pytest.raises(BudgetExceeded) as exc:
        await store.consume("alice", per_user=3, per_day=100)
    assert exc.value.scope == "user"


async def test_one_users_limit_does_not_deny_another():
    """The reason per-user comes before the global ceiling."""
    store = UsageStore(_DB())
    for _ in range(3):
        await store.consume("alice", per_user=3, per_day=100)
    with pytest.raises(BudgetExceeded):
        await store.consume("alice", per_user=3, per_day=100)

    await store.consume("bob", per_user=3, per_day=100)


async def test_the_global_ceiling_catches_many_small_accounts():
    """Per-user limits multiply by however many accounts someone registers, so
    the ceiling behind them is the one that actually bounds the bill."""
    store = UsageStore(_DB())
    for i in range(5):
        await store.consume("user-{}".format(i), per_user=10, per_day=5)

    with pytest.raises(BudgetExceeded) as exc:
        await store.consume("user-99", per_user=10, per_day=5)
    assert exc.value.scope == "global"


async def test_a_failed_call_still_counts():
    """Charged before the call, not after. Billing only successes would let a
    caller retry a failing prompt without limit."""
    store = UsageStore(_DB())
    await store.consume("alice", per_user=1, per_day=100)
    # The caller's request may have blown up upstream; the attempt is still spent.
    with pytest.raises(BudgetExceeded):
        await store.consume("alice", per_user=1, per_day=100)


async def test_zero_means_unlimited_for_local_use():
    """A developer running this on their laptop should not hit a budget."""
    store = UsageStore(_DB())
    for _ in range(50):
        await store.consume("alice", per_user=0, per_day=0)


# ── Cache ────────────────────────────────────────────────────────────────────


async def test_an_explanation_round_trips():
    store = UsageStore(_DB())
    key = store.key("why", "pred-1", "v4")
    assert await store.cached(key) is None

    await store.store(key, {"answer": "Russell starts on pole."})
    assert (await store.cached(key))["answer"] == "Russell starts on pole."


def test_the_key_separates_windows_and_model_versions():
    """Two windows are different forecasts and deserve different explanations;
    a retrain must not serve stale reasoning about superseded numbers."""
    store = UsageStore(_DB())
    assert store.key("why", "pred-post", "v4") != store.key("why", "pred-final", "v4")
    assert store.key("why", "pred-1", "v4") != store.key("why", "pred-1", "v5")
