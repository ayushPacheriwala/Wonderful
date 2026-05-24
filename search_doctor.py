"""
Agent tool for searching doctors from audio-transcribed input.

Entry points:
    handle_query(text)     — full natural language query (all three scenarios)
    search_doctor(query)   — name-only phonetic search (original, backward-compatible)

Scenarios handled by handle_query():
    1. "I want to speak to Dr. Ionut Dumitrescu from Cardiology"
    2. "I want to speak to the Cardiology doctor at Clinica Cluj Care"
    3. "Recommend me a doctor from Cardiology near Cluj"
"""

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

import jellyfish
import phonetics

DB_PATH = "healthcare.db"
_PHONETIC_CANDIDATE_LIMIT = 50

_DAY_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_FULL = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_AVAIL_RE = re.compile(
    r"(?P<d1>\w{3})-(?P<d2>\w{3})\s+(?P<h1>\d{1,2}):(?P<m1>\d{2})-(?P<h2>\d{1,2}):(?P<m2>\d{2})"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _dm_codes(token: str) -> List[str]:
    primary, alt = phonetics.dmetaphone(token)
    return [c for c in (primary, alt) if c]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for k in ("first_dm", "first_dm_alt", "last_dm", "last_dm_alt"):
        d.pop(k, None)
    d["languages"] = json.loads(d["languages"])
    return d


def _jaro_winkler_score(row: sqlite3.Row, query: str) -> float:
    full = f"{row['first_name']} {row['last_name']}".lower()
    q = query.lower()
    return max(
        jellyfish.jaro_winkler_similarity(full, q),
        jellyfish.jaro_winkler_similarity(row["first_name"].lower(), q),
        jellyfish.jaro_winkler_similarity(row["last_name"].lower(), q),
    )


# ---------------------------------------------------------------------------
# Phonetic name search (unchanged from original)
# ---------------------------------------------------------------------------

def _phonetic_search(conn: sqlite3.Connection, query_tokens: List[str]) -> List[sqlite3.Row]:
    token_dm_sets = [_dm_codes(t) for t in query_tokens]
    seen: Dict[int, sqlite3.Row] = {}

    # Strict: one token → first_name DM AND another → last_name DM
    if len(token_dm_sets) >= 2:
        for i, first_codes in enumerate(token_dm_sets):
            for j, last_codes in enumerate(token_dm_sets):
                if i == j or not first_codes or not last_codes:
                    continue
                ph = ",".join("?" * len(first_codes))
                lh = ",".join("?" * len(last_codes))
                for row in conn.execute(
                    f"SELECT * FROM doctors WHERE first_dm IN ({ph}) AND last_dm IN ({lh})"
                    f" LIMIT {_PHONETIC_CANDIDATE_LIMIT}",
                    first_codes + last_codes,
                ):
                    seen.setdefault(row["id"], row)

    # Loose: any token matches either name field
    all_codes = [c for codes in token_dm_sets for c in codes]
    if all_codes:
        ph = ",".join("?" * len(all_codes))
        for row in conn.execute(
            f"SELECT * FROM doctors WHERE first_dm IN ({ph}) OR last_dm IN ({ph})"
            f" LIMIT {_PHONETIC_CANDIDATE_LIMIT}",
            all_codes + all_codes,
        ):
            seen.setdefault(row["id"], row)

    return list(seen.values())


def _fts_search(conn: sqlite3.Connection, query: str) -> List[sqlite3.Row]:
    tokens = query.strip().split()
    for fts_q in (" ".join(f'"{t}"*' for t in tokens), query):
        try:
            rows = conn.execute(
                "SELECT d.* FROM doctors_fts fts JOIN doctors d ON d.id = fts.rowid"
                " WHERE doctors_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_q, _PHONETIC_CANDIDATE_LIMIT),
            ).fetchall()
            if rows:
                return rows
        except sqlite3.OperationalError:
            continue
    return []


# ---------------------------------------------------------------------------
# Multi-field search (new)
# ---------------------------------------------------------------------------

def _get_distinct(conn: sqlite3.Connection, column: str) -> List[str]:
    return [r[0] for r in conn.execute(f"SELECT DISTINCT {column} FROM doctors")]


def _fuzzy_match(value: str, candidates: List[str], threshold: float = 0.75) -> Optional[str]:
    """
    Return the closest candidate to value by Jaro-Winkler similarity,
    or None if no candidate exceeds the threshold.
    Used to normalise audio-transcribed speciality/location values to known DB values.
    """
    if not value or not candidates:
        return None
    best, score = max(
        ((c, jellyfish.jaro_winkler_similarity(value.lower(), c.lower())) for c in candidates),
        key=lambda x: x[1],
    )
    return best if score >= threshold else None


def _day_index(day: Optional[str]) -> Optional[int]:
    if not day:
        return None
    d = day.strip().lower()
    if d in _DAY_FULL:
        return _DAY_FULL[d]
    return _DAY_ABBR.get(d[:3])


def _time_minutes(t: Optional[str]) -> Optional[int]:
    if not t:
        return None
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t.strip(), re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    suffix = (m.group(3) or "").lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute


def _availability_matches(
    availability: Optional[str],
    day_idx: Optional[int],
    time_min: Optional[int],
) -> bool:
    """True when the doctor's availability covers the requested day and/or time."""
    if day_idx is None and time_min is None:
        return True
    if not availability:
        return False
    m = _AVAIL_RE.search(availability)
    if not m:
        return True  # unparseable strings are not filtered out
    d1 = _DAY_ABBR.get(m.group("d1").lower())
    d2 = _DAY_ABBR.get(m.group("d2").lower())
    open_min = int(m.group("h1")) * 60 + int(m.group("m1"))
    close_min = int(m.group("h2")) * 60 + int(m.group("m2"))
    if day_idx is not None and (d1 is None or d2 is None or not (d1 <= day_idx <= d2)):
        return False
    if time_min is not None and not (open_min <= time_min <= close_min):
        return False
    return True


def search_by_fields(
    conn: sqlite3.Connection,
    name: Optional[str] = None,
    speciality: Optional[str] = None,
    clinic: Optional[str] = None,
    location: Optional[str] = None,
    intent: str = "specific",
    min_experience: Optional[int] = None,
    min_rating: Optional[float] = None,
    open_day: Optional[str] = None,
    open_time: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Compose a SQL query from whichever fields were extracted from the user query.

    name           → Double Metaphone phonetic match, then Jaro-Winkler re-rank
    speciality     → fuzzy-normalised to the 20 known values, then exact SQL filter
    clinic         → FTS5 search on clinic_name column
    location       → FTS5 search on location column
    min_experience → SQL filter on years_experience
    min_rating     → SQL filter on rating
    open_day       → post-filter against the doctor's availability range
    open_time      → post-filter against the doctor's availability range
    intent         → "recommend" sorts by rating DESC; others keep phonetic rank first
    """
    conn.row_factory = sqlite3.Row
    core_conditions: List[str] = []
    filter_conditions: List[str] = []
    params: List[Any] = []

    # --- Name: phonetic candidates become an id allowlist ---
    name_candidate_ids: Optional[List[int]] = None
    if name:
        tokens = name.strip().split()
        candidates = _phonetic_search(conn, tokens)
        if candidates:
            name_candidate_ids = [r["id"] for r in candidates]
            ph = ",".join("?" * len(name_candidate_ids))
            core_conditions.append(f"d.id IN ({ph})")
            params.extend(name_candidate_ids)

    # --- Speciality: fuzzy-match to known values, then exact filter ---
    if speciality:
        known = _get_distinct(conn, "speciality")
        matched = _fuzzy_match(speciality, known)
        if matched:
            core_conditions.append("d.speciality = ?")
            params.append(matched)

    # --- Clinic + location: FTS5 over indexed columns ---
    fts_terms = []
    if clinic:
        fts_terms.append(clinic)
    if location:
        fts_terms.append(location)

    if fts_terms:
        fts_q = " OR ".join(f'"{t}"*' for t in fts_terms)
        core_conditions.append(
            "d.id IN (SELECT rowid FROM doctors_fts WHERE doctors_fts MATCH ?)"
        )
        params.append(fts_q)

    if not core_conditions:
        return []

    # --- Numeric filters ---
    if min_experience is not None:
        filter_conditions.append("d.years_experience >= ?")
        params.append(int(min_experience))
    if min_rating is not None:
        filter_conditions.append("d.rating >= ?")
        params.append(float(min_rating))

    where = "WHERE " + " AND ".join(core_conditions + filter_conditions)

    # Fetch more rows than needed when we'll re-rank by name similarity
    # or post-filter by availability
    needs_post_filter = open_day or open_time
    base_fetch = len(name_candidate_ids) if name_candidate_ids else limit * 4
    fetch_limit = base_fetch * 4 if needs_post_filter else base_fetch
    order = "ORDER BY d.rating DESC"

    rows = conn.execute(
        f"SELECT d.* FROM doctors d {where} {order} LIMIT ?",
        params + [fetch_limit],
    ).fetchall()

    # Re-rank by name similarity when a name was part of the query
    if name and rows:
        rows = sorted(rows, key=lambda r: _jaro_winkler_score(r, name), reverse=True)

    # Post-filter on availability
    if needs_post_filter:
        day_idx = _day_index(open_day)
        time_min = _time_minutes(open_time)
        rows = [r for r in rows if _availability_matches(r["availability"], day_idx, time_min)]

    return [_row_to_dict(r) for r in rows[:limit]]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_doctor(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Name-only phonetic search. Backward-compatible entry point.
    For full natural language queries use handle_query() instead.
    """
    if not query or not query.strip():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    candidates = _phonetic_search(conn, query.strip().split())
    if not candidates:
        candidates = _fts_search(conn, query)
    conn.close()

    if not candidates:
        return []

    ranked = sorted(candidates, key=lambda r: _jaro_winkler_score(r, query), reverse=True)
    return [_row_to_dict(r) for r in ranked[:limit]]


def handle_query(text: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Main entry point for the agent. Parses natural language then searches.

    Handles:
      1. "I want to speak to Dr. Ionut Dumitrescu from Cardiology"
      2. "I want to speak to the Cardiology doctor at Clinica Cluj Care"
      3. "Recommend me a doctor from Cardiology near Cluj"
    """
    from query_parser import parse_query

    parsed = parse_query(text)

    conn = sqlite3.connect(DB_PATH)
    results = search_by_fields(
        conn,
        name=parsed.get("name"),
        speciality=parsed.get("speciality"),
        clinic=parsed.get("clinic"),
        location=parsed.get("location"),
        intent=parsed.get("intent", "specific"),
        limit=limit,
    )
    conn.close()
    return results


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Ionut Dumitrescu"
    results = search_doctor(q)
    print(f"Query: {q!r}  →  {len(results)} result(s)\n")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['full_name']} | {r['speciality']} | {r['clinic_name']} | rating {r['rating']}")
