"""
Builds healthcare.db from healthcare_data.json.
Run this script whenever the source JSON is updated.

Tables created:
  doctors      — main records + precomputed Double Metaphone codes
  doctors_fts  — FTS5 virtual table for full-text fallback search
"""

import json
import sqlite3
import phonetics

JSON_PATH = "healthcare_data.json"
DB_PATH = "healthcare.db"

DDL = """
PRAGMA journal_mode = WAL;

DROP TABLE IF EXISTS doctors_fts;
DROP TABLE IF EXISTS doctors;

CREATE TABLE doctors (
    id               INTEGER PRIMARY KEY,
    first_name       TEXT NOT NULL,
    last_name        TEXT NOT NULL,
    full_name        TEXT NOT NULL,
    first_dm         TEXT NOT NULL,
    first_dm_alt     TEXT,
    last_dm          TEXT NOT NULL,
    last_dm_alt      TEXT,
    clinic_name      TEXT,
    location         TEXT,
    speciality       TEXT,
    address          TEXT,
    phone            TEXT,
    email            TEXT,
    postal_code      TEXT,
    county           TEXT,
    years_experience INTEGER,
    education        TEXT,
    languages        TEXT,
    availability     TEXT,
    rating           REAL
);

CREATE INDEX idx_first_dm ON doctors(first_dm);
CREATE INDEX idx_first_dm_alt ON doctors(first_dm_alt);
CREATE INDEX idx_last_dm  ON doctors(last_dm);
CREATE INDEX idx_last_dm_alt  ON doctors(last_dm_alt);
"""

FTS_DDL = """
CREATE VIRTUAL TABLE doctors_fts USING fts5(
    full_name,
    clinic_name,
    location,
    speciality,
    content = doctors,
    content_rowid = id
);

INSERT INTO doctors_fts(rowid, full_name, clinic_name, location, speciality)
SELECT id, full_name, clinic_name, location, speciality FROM doctors;
"""

INSERT_SQL = """
INSERT INTO doctors (
    first_name, last_name, full_name,
    first_dm, first_dm_alt,
    last_dm,  last_dm_alt,
    clinic_name, location, speciality,
    address, phone, email,
    postal_code, county, years_experience,
    education, languages, availability, rating
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _dm(name: str) -> tuple:
    primary, alt = phonetics.dmetaphone(name)
    return primary, alt or None


def build():
    print(f"Loading {JSON_PATH}…")
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for r in data:
        f_dm, f_dm_alt = _dm(r["first_name"])
        l_dm, l_dm_alt = _dm(r["last_name"])
        rows.append((
            r["first_name"],
            r["last_name"],
            f"{r['first_name']} {r['last_name']}",
            f_dm, f_dm_alt,
            l_dm, l_dm_alt,
            r["clinic_name"],
            r["location"],
            r["speciality"],
            r["address"],
            r["phone"],
            r["email"],
            r["postal_code"],
            r["county"],
            r["years_experience"],
            r["education"],
            json.dumps(r["languages"], ensure_ascii=False),
            r["availability"],
            r["rating"],
        ))

    print(f"Writing {DB_PATH}…")
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DDL)
    conn.executemany(INSERT_SQL, rows)
    conn.executescript(FTS_DDL)
    conn.commit()
    conn.close()

    print(f"Done — {len(rows):,} records inserted.")


if __name__ == "__main__":
    build()
