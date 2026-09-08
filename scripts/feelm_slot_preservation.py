"""Research-only pre-exclusion selection, with fixed 500/100 budgets.

Input order is the caller's existing score order. No scoring or service IO.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RankedCandidate:
    movie_id: int
    role: str | None


def build_reserved_top100(raw500):
    rows = tuple(raw500)
    if len(rows) > 500:
        raise ValueError('raw candidate budget is 500')
    seen = set()
    for row in rows:
        if type(row) is not RankedCandidate or type(row.movie_id) is not int or row.movie_id <= 0:
            raise ValueError('invalid typed candidate or movie ID')
        if row.role not in ('TASTE', 'DISCOVERY', None):
            raise ValueError('unknown role')
        if row.movie_id in seen:
            raise ValueError('duplicate movie ID')
        seen.add(row.movie_id)
    eligible = tuple(row for row in rows if row.role is not None)
    reserved = set()
    for role, limit in (('TASTE', 3), ('DISCOVERY', 1)):
        reserved.update(row.movie_id for row in tuple(r for r in eligible if r.role == role)[:limit])
    selected = set(reserved)
    for row in eligible:
        if len(selected) == 100:
            break
        selected.add(row.movie_id)
    return tuple(row for row in eligible if row.movie_id in selected)
