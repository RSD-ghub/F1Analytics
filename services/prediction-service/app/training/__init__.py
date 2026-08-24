"""Model training.

Imported only by the training entry point, never by the serving path. The
service loads fitted weights from a JSON artifact and needs neither scipy nor
this package at runtime — which is why ``requirements.txt`` does not list them.
"""
