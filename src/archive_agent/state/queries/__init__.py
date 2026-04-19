"""Per-entity query modules. Every DB read/write in the app goes through
one of these — no ad-hoc ``sqlite3`` calls outside ``state/``.
"""
