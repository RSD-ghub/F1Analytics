"""FIA regulations: fetching and chunking (``source``), storage and retrieval
(``store``), and the read API over them (``router``).

Self-contained on purpose — see ``store`` for why it lives in this process and
what would move it out of one.
"""

from app.regulations.store import RegulationStore

__all__ = ["RegulationStore"]
