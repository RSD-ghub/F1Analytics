"""Shared infrastructure for the F1 forecasting platform.

Deliberately scoped to mechanical concerns only — settings loading, Mongo client
construction, health reporting, JWT verification, and the wire-format models that
services exchange. Domain logic (feature engineering, the forecast model, scoring
maths, ingestion transforms) stays inside the service that owns it; that boundary
is the point of the split.
"""

__version__ = "0.1.0"
