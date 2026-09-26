"""What the model thinks a circuit is like.

Only the model's own opinion lives here: the archetype label and the measured
geometry it was clustered on. Everything else about a circuit — its shape, its
race history — belongs to ingestion, which owns the data it comes from. This
endpoint exists so the page can say "power circuit" without core-api reading a
modelling artifact out of another service's filesystem.

Worth stating plainly, because the page will quote it: the archetype is a
clustering of three measured quantities, not a claim that the features derived
from it improved anything. They did not, twice, and they are withheld from the
model. The label is a good description of a circuit and nothing more.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter

from app.services import circuits as archetypes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/circuits", tags=["circuits"])

_LOADED = None


def _artifact():
    global _LOADED
    if _LOADED is None:
        _LOADED = archetypes.CircuitArchetypes.load()
    return _LOADED


@router.get("/{circuit}", response_model=Dict[str, Any])
async def character(circuit: str) -> Dict[str, Any]:
    """A circuit's archetype and the metrics behind it.

    Never 404s. A circuit outside the artifact — a new venue, or one we have
    too little data for — returns ``unknown`` with no metrics, which the page
    renders as an absent panel rather than an error.
    """
    artifact = _artifact()
    key = archetypes.normalise(circuit)
    return {
        "circuit": circuit,
        "archetype": artifact.archetype_of(circuit),
        "metrics": artifact.metrics.get(key) or {},
        "artifact_version": artifact.version,
        "labels": artifact.labels(),
    }
