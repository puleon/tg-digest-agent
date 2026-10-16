"""Corpus refresh: collect → views → enrich → links/entities → dedup → index → profiles.

Run by a systemd user unit (daily timer, or ``/update`` in the bot through the API); the state
lives in files under ``data/refresh/`` so the API can report on a run it does not own.
"""
