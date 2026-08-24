#!/usr/bin/env python
"""Prove the Tinker/Inkling wire contract with one real call.

    python scripts/verify_inkling.py

The contract was reconstructed from Tinker's documentation after an earlier
version of the client guessed the base URL, the endpoint path and the model
name — and got all three wrong. Nothing in the test suite can catch that, since
every test necessarily stubs the provider. Only a live call can.

Prints the shape of the key, never its value.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared", "f1_common"))


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main() -> int:
    load_env()
    from f1_common.llm import TinkerClient

    key = os.environ.get("TINKER_API_KEY", "")
    base = os.environ.get(
        "TINKER_BASE_URL",
        "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1",
    )
    model = os.environ.get("INKLING_MODEL", "thinkingmachines/Inkling")
    effort = os.environ.get("INKLING_REASONING_EFFORT", "medium")

    print("key    : %s" % ("set, %d chars" % len(key) if key else "EMPTY"))
    print("base   : %s" % base)
    print("model  : %s" % model)
    print("effort : %s" % effort)
    print()

    if not key:
        print("No TINKER_API_KEY in .env — paste it and re-run.")
        return 1

    client = TinkerClient(key, base, model, reasoning_effort=effort)
    print("calling %s%s ..." % (base, "/chat/completions"))
    try:
        completion = await client.complete_verbose(
            prompt=(
                "Question: Which driver had the better race pace?\n\n"
                "facts (the only information you may use for this answer):\n"
                "Alpha: 96.6s median over 11 long-run laps\n"
                "Bravo: 97.2s median over 11 long-run laps\n"
            ),
            system=(
                "You are a Formula 1 race strategist. Use only the facts given. "
                "Answer in one sentence."
            ),
            max_tokens=200,
        )
    except Exception as exc:
        detail = str(exc)
        print("FAILED: %s" % detail[:300])
        print()
        if "402" in detail or "Payment Required" in detail:
            print("402 -> GOOD NEWS, mostly. The key authenticated and the")
            print("       endpoint and model resolved; only credit is missing.")
            print("       The wire contract is confirmed correct.")
            print("       Add a billing method or credits at:")
            print("       https://tinker-console.thinkingmachines.ai")
        elif "404" in detail:
            print("404 -> the endpoint path or model name is wrong.")
        elif "401" in detail or "403" in detail:
            print("401/403 -> the key is rejected. Check it is active in the console.")
        elif "timeout" in detail.lower():
            print("timeout -> reachable but slow; try INKLING_REASONING_EFFORT=low.")
        else:
            print("Check the base URL is reachable from this machine.")
        return 1

    print("ANSWER   : %s" % completion.text)
    print("REASONING: %s" % (
        (completion.reasoning[:160] + "...") if completion.reasoning
        else "(none returned — separate_reasoning may be unsupported for this model)"
    ))
    print()
    print("Contract confirmed. Bernie is live.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
