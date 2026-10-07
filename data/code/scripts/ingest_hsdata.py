#!/usr/bin/env python3
"""
Walk hsdata git history, parse CardDefs.xml at each patch commit,
ingest COLLECTIBLE=1 English cards into DuckDB.

Usage:
    export DB_PATH=/path/to/powercreep.duckdb
    python scripts/ingest_hsdata.py [--skip-clone] [--workers 8] [--fresh]
"""

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
from lxml import etree
from tqdm import tqdm

# ─────────────────────────────────────────────────────────────────────────────
# Paths & config
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR   = Path(__file__).resolve().parent.parent / "data" / "hsdata"
HSDATA_URL = "https://github.com/HearthSim/hsdata"

MIN_PATCH = "1.0.0.4944"
MAX_PATCH = "36.4.2.251332"

DB_PATH = os.environ.get(
    "DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "powercreep.duckdb"),
)

# ─────────────────────────────────────────────────────────────────────────────
# Enum maps (from python-hearthstone enums.py)
# ─────────────────────────────────────────────────────────────────────────────

# CardSet int → full display name (from python-hearthstone enums.py)
CARD_SET = {
    0:    "Invalid",
    1:    "Test Temporary",
    2:    "Basic",
    3:    "Classic",
    4:    "Hall of Fame",
    5:    "Missions",
    11:   "Promo",
    12:   "Curse of Naxxramas",
    13:   "Goblins vs Gnomes",
    14:   "Blackrock Mountain",
    15:   "The Grand Tournament",
    18:   "Tavern Brawl",
    20:   "The League of Explorers",
    21:   "Whispers of the Old Gods",
    23:   "One Night in Karazhan",
    25:   "Mean Streets of Gadgetzan",
    27:   "Journey to Un'Goro",
    1001: "Knights of the Frozen Throne",
    1004: "Kobolds and Catacombs",
    1125: "The Witchwood",
    1127: "The Boomsday Project",
    1129: "Rastakhan's Rumble",
    1130: "Rise of Shadows",
    1143: "Taverns of Time",
    1158: "Saviours of Uldum",
    1347: "Descent of Dragons",
    1403: "Year of the Dragon",
    1414: "Ashes of Outland",
    1439: "Wild Event",
    1443: "Scholomance Academy",
    1453: "Battlegrounds",
    1463: "Demon Hunter Initiate",
    1466: "Madness at the Darkmoon Faire",
    1525: "Forged in the Barrens",
    1559: "Wailing Caverns",
    1578: "United in Stormwind",
    1586: "Mercenaries",
    1626: "Fractured in Alterac Valley",
    1635: "Legacy",
    1637: "Core",
    1646: "Vanilla",
    1658: "Voyage to the Sunken City",
    1691: "Murder at Castle Nathria",
    1776: "March of the Lich King",
    1809: "Festival of Legends",
    1810: "Core Hidden",
    1858: "Titans",
    1869: "Path of Arthas",
    1892: "Wild West",
    1897: "Whizbang's Workshop",
    1898: "Wonders",
    1905: "Perils in Paradise",
    1935: "Great Dark Beyond",
    1941: "Event",
    1946: "Into the Emerald Dream",
    1952: "The Shrouded City",
    1957: "Across the Timeways",
    1980: "Cataclysm",
    1988: "Escape from Violet Hold",
}

CARD_CLASS = {
    0: "INVALID", 1: "DEATHKNIGHT", 2: "DRUID", 3: "HUNTER",
    4: "MAGE", 5: "PALADIN", 6: "PRIEST", 7: "ROGUE",
    8: "SHAMAN", 9: "WARLOCK", 10: "WARRIOR", 11: "DREAM",
    12: "NEUTRAL", 13: "WHIZBANG", 14: "DEMONHUNTER",
}

# Only these card types are collectible and comparable.
CARD_TYPE = {
    3: "HERO", 4: "MINION", 5: "SPELL", 7: "WEAPON", 39: "LOCATION",
}
COLLECTIBLE_TYPES = frozenset(CARD_TYPE)

# CardSet 17 = HERO_SKINS: cosmetic alternate hero portraits (e.g. the Death
# Knight Arthas skins, Madame Lazul, Judgement Uther). They are flagged
# COLLECTIBLE=1 because you unlock the portrait, but they carry no gameplay text
# or stats and are not real cards, so they pollute HERO comparisons. Exclude
# them. Older patches encode CARD_SET as a String ("HERO_SKINS"); newer ones as
# the Int 17 — handle both.
EXCLUDED_CARD_SETS = frozenset({17, "HERO_SKINS"})

RARITY = {
    0: "INVALID", 1: "COMMON", 2: "FREE", 3: "RARE",
    4: "EPIC", 5: "LEGENDARY", 6: "UNKNOWN_6",
}

# Full race map (from python-hearthstone enums.py). Non-"visible" races still
# appear in CardDefs.xml on some cards, so they must be mapped rather than
# leaking through as raw integer strings.
RACE = {
    0: "INVALID", 1: "BLOODELF", 2: "DRAENEI", 3: "DWARF", 4: "GNOME",
    5: "GOBLIN", 6: "HUMAN", 7: "NIGHTELF", 8: "ORC", 9: "TAUREN",
    10: "TROLL", 11: "UNDEAD", 12: "WORGEN", 13: "GOBLIN2", 14: "MURLOC",
    15: "DEMON", 16: "SCOURGE", 17: "MECHANICAL", 18: "ELEMENTAL", 19: "OGRE",
    20: "BEAST", 21: "TOTEM", 22: "NERUBIAN", 23: "PIRATE", 24: "DRAGON",
    25: "BLANK", 26: "ALL", 38: "EGG", 43: "QUILBOAR", 80: "CENTAUR",
    81: "FURBOLG", 83: "HIGHELF", 84: "TREANT", 85: "OWLKIN", 88: "HALFORC",
    89: "LOCK", 92: "NAGA", 93: "OLDGOD", 94: "PANDAREN", 95: "GRONN",
    96: "CELESTIAL", 97: "GNOLL", 98: "GOLEM", 99: "HARPY", 100: "VULPERA",
}


# ─────────────────────────────────────────────────────────────────────────────
# Card text cleaning
# ─────────────────────────────────────────────────────────────────────────────

# A '@' that separates two full renderings of the same card (hand-text vs
# play-text) or trailing state annotations. It sits at a clause/tag boundary
# (preceded by . ) ] > !) and is followed by a new formatted block ([x], a tag,
# a capital letter) or end-of-string. Everything from the first such separator
# on is a duplicate/state variant and is dropped, keeping the first rendering.
# A lone inline '@' (e.g. "draws @ Candles") is a runtime display value. It
# does not match this pattern and is stripped separately. ``$@`` is different:
# it is a card's static script-data value and is resolved before this cleaner.
_VARIANT_SEP_RE = re.compile(r"(?<=[.)\]>!])\s*@\s*(?=\[x\]|<b>|<i>|[A-Z]|$)")
_SCRIPT_DATA_NUM_1_PLACEHOLDER_RE = re.compile(r"\$@")
# Pluralization directive: |4(Candle, Candles) — keep the plural form.
_PLURAL_RE      = re.compile(r"\|\d+\(([^,)]*),\s*([^)]*)\)")
_BRACE_RE       = re.compile(r"\{\d+\}")          # {0} {1} runtime stat placeholders
_EMPTY_PARENS   = re.compile(r"\(\s*\)")          # () left after placeholder removal
_EMPTY_BOLD     = re.compile(r"<b>\s*</b>")
_EMPTY_ITALIC   = re.compile(r"<i>\s*</i>")
_WS_RE          = re.compile(r"\s+")
_SPACE_PUNCT_RE = re.compile(r"\s+([.,;:!?])")


def expand_script_data_placeholders(text, tags, card_id, patch_id):
    """Replace static CardDefs ``$@`` placeholders with their script data.

    CardDefs uses ``$@`` for ``TAG_SCRIPT_DATA_NUM_1`` (for example, Torch's
    text has ``$@`` and its tag value is 8). Literal values such as ``$3`` are
    intentionally left for ``clean_card_text`` to render as ``3``.
    """
    if "$@" not in text:
        return text
    value = tags.get("TAG_SCRIPT_DATA_NUM_1")
    if value is None:
        raise ValueError(
            f"{patch_id} {card_id}: $@ in card text without "
            "TAG_SCRIPT_DATA_NUM_1"
        )
    return _SCRIPT_DATA_NUM_1_PLACEHOLDER_RE.sub(str(value), text)


def clean_card_text(text):
    """Strip templating artifacts from raw CardDefs text while preserving the
    <b>…</b> tags that keyword extraction relies on.

    Removes: @-separated duplicate/state renderings, [x] format markers,
    {N} stat placeholders, $/# damage-scaling markers, |N(a, b) pluralization
    directives, inline @ runtime values, _ (non-breaking space), and empty
    tag/paren shells. Static ``$@`` values must be resolved first with
    ``expand_script_data_placeholders``. Idempotent — cleaning already-clean
    text is a no-op.
    """
    if not text:
        return text
    t = _VARIANT_SEP_RE.split(text, maxsplit=1)[0]
    t = t.replace("[x]", "")
    t = _BRACE_RE.sub("", t)
    t = _PLURAL_RE.sub(r"\2", t)
    t = t.replace("$", "").replace("#", "")
    t = t.replace("@", "")
    t = t.replace("_", " ")
    t = _EMPTY_PARENS.sub("", t)
    t = _EMPTY_BOLD.sub("", t)
    t = _EMPTY_ITALIC.sub("", t)
    t = _WS_RE.sub(" ", t)
    t = _SPACE_PUNCT_RE.sub(r"\1", t)
    return t.strip()


# ─────────────────────────────────────────────────────────────────────────────
# DB schema
# ─────────────────────────────────────────────────────────────────────────────

DDL_CREATE = """
CREATE TABLE IF NOT EXISTS cards (
    patch_id           TEXT     NOT NULL,
    name               TEXT     NOT NULL,
    type               TEXT,
    text               TEXT,
    health             INTEGER,
    attack             INTEGER,
    mana               INTEGER,
    armor              INTEGER,
    card_set           TEXT,
    class              TEXT,
    rarity             TEXT,
    tribe              TEXT
);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Git helpers
# ─────────────────────────────────────────────────────────────────────────────

def clone_or_pull():
    if not REPO_DIR.exists():
        print(f"Cloning {HSDATA_URL} → {REPO_DIR} (this may take a while) …")
        subprocess.run(["git", "clone", HSDATA_URL, str(REPO_DIR)], check=True)
    else:
        print("Updating hsdata …")
        subprocess.run(["git", "pull", "--ff-only"], cwd=str(REPO_DIR), check=True)


def get_patch_commits():
    """Return list of (hash, version_str) in chronological order (oldest first)."""
    result = subprocess.run(
        ["git", "log", "--format=%H|||%s", "--reverse"],
        cwd=str(REPO_DIR), capture_output=True, text=True, check=True,
    )
    commits = []
    for line in result.stdout.splitlines():
        if "|||" not in line:
            continue
        h, msg = line.split("|||", 1)
        m = re.search(r"patch\s+(\d+\.\d+\.\d+\.\d+)", msg, re.IGNORECASE)
        if m:
            commits.append((h.strip(), m.group(1)))
    return commits


def get_xml_bytes(commit_hash):
    result = subprocess.run(
        ["git", "show", f"{commit_hash}:CardDefs.xml"],
        cwd=str(REPO_DIR), capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def read_and_parse_patch(patch):
    """Read and parse one hsdata revision in a worker process.

    DuckDB has a single writer in the parent process; workers only perform the
    independent Git object read and XML parsing work.
    """
    commit_hash, version = patch
    xml_bytes = get_xml_bytes(commit_hash)
    if xml_bytes is None:
        return version, None
    return version, parse_carddefs(xml_bytes, version)

# ─────────────────────────────────────────────────────────────────────────────
# XML parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_carddefs(xml_bytes, patch_id):
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        print(f"  XML parse error for {patch_id}: {exc}", file=sys.stderr)
        return []

    cards = []
    for entity in root.iter("Entity"):
        if not entity.get("CardID"):
            continue

        tags = {}
        for tag in entity.findall("Tag"):
            tag_name = tag.get("name")
            tag_type = tag.get("type")

            if tag_type == "LocString":
                en = tag.findtext("enUS")
                if en is not None:
                    tags[tag_name] = en.strip()
            elif tag_type == "Int":
                val = tag.get("value")
                if val is not None:
                    try:
                        tags[tag_name] = int(val)
                    except ValueError:
                        pass
            elif tag_type == "String":
                if tag.text:
                    tags[tag_name] = tag.text.strip()

        if tags.get("COLLECTIBLE") != 1:
            continue

        # Skip non-comparable card types (e.g. ENCHANTMENT=6) that are flagged
        # collectible in some builds; only MINION/SPELL/WEAPON/HERO/LOCATION count.
        if tags.get("CARDTYPE") not in COLLECTIBLE_TYPES:
            continue

        # Skip cosmetic hero-portrait skins (CardSet HERO_SKINS): collectible but
        # textless and statless, so they only add noise to HERO comparisons.
        if tags.get("CARD_SET") in EXCLUDED_CARD_SETS:
            continue

        def xlat(mapping, key):
            raw = tags.get(key)
            if raw is None:
                return None
            return mapping.get(raw, str(raw))

        raw_text = tags.get("CARDTEXT") or tags.get("CARDTEXT_INHAND")
        if raw_text:
            raw_text = expand_script_data_placeholders(
                raw_text, tags, entity.get("CardID"), patch_id,
            )
            raw_text = clean_card_text(raw_text.replace("\n", " "))

        cards.append((
            patch_id,
            tags.get("CARDNAME"),
            xlat(CARD_TYPE, "CARDTYPE"),
            raw_text,
            tags.get("HEALTH"),
            tags.get("ATK"),
            # CardDefs omits COST when it is zero (for example, Pounce).  A
            # missing tag therefore means a 0-cost card, not unknown mana.
            tags.get("COST", 0),
            tags.get("ARMOR"),
            xlat(CARD_SET, "CARD_SET"),
            xlat(CARD_CLASS, "CLASS"),
            xlat(RARITY, "RARITY"),
            xlat(RACE, "CARDRACE"),
        ))

    return cards

# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def deduplicate(conn):
    """Keep only the latest patch where each (name, type, text, health, attack,
    mana, armor) combination appeared. Tribe is excluded from the key so that a
    card whose tribe changed across builds collapses to a single row with the most
    recent tribe value."""
    total = conn.execute("SELECT count(*) FROM cards").fetchone()[0]
    conn.execute("""
        CREATE TABLE cards_dedup AS
        SELECT patch_id, name, type, text, health, attack, mana, armor,
               card_set, class, rarity, tribe
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY name, type, text, health, attack, mana, armor
                       ORDER BY
                           CAST(string_split(patch_id, '.')[1] AS INTEGER) DESC,
                           CAST(string_split(patch_id, '.')[2] AS INTEGER) DESC,
                           CAST(string_split(patch_id, '.')[3] AS INTEGER) DESC,
                           CAST(string_split(patch_id, '.')[4] AS INTEGER) DESC
                   ) AS rn
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY patch_id, name
                           ORDER BY rowid
                       ) AS patch_name_rn
                FROM cards
            )
            WHERE patch_name_rn = 1
        )
        WHERE rn = 1
    """)
    remaining = conn.execute("SELECT count(*) FROM cards_dedup").fetchone()[0]
    conn.execute("DROP TABLE cards")
    conn.execute("ALTER TABLE cards_dedup RENAME TO cards")
    conn.execute("ALTER TABLE cards ADD PRIMARY KEY (patch_id, name)")
    return total - remaining

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-clone", action="store_true", help="Don't git clone/pull")
    ap.add_argument(
        "--fresh", action="store_true",
        help="Remove the existing DuckDB database before ingesting",
    )
    ap.add_argument(
        "--workers", type=int, default=8,
        help="Processes for independent Git reads and XML parsing (default: 8)",
    )
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers must be at least 1")

    if not args.skip_clone:
        clone_or_pull()

    if args.fresh:
        db_path = Path(DB_PATH)
        for path in (db_path, db_path.with_suffix(db_path.suffix + ".wal")):
            if path.exists():
                path.unlink()
                print(f"Removed existing database file {path}.")

    print(f"Opening database at {DB_PATH} …")
    conn = duckdb.connect(DB_PATH)

    print("Wiping table and recreating schema …")
    conn.execute("DROP TABLE IF EXISTS cards")
    conn.execute(DDL_CREATE)
    print("Schema ready.")

    min_v = tuple(int(x) for x in MIN_PATCH.split("."))
    max_v = tuple(int(x) for x in MAX_PATCH.split("."))
    patches = [
        (h, v) for h, v in get_patch_commits()
        if min_v <= tuple(int(x) for x in v.split(".")) <= max_v
    ]
    print(f"Found {len(patches)} patch commits in hsdata (>= {MIN_PATCH}).")

    total_cards = 0
    done_patches = 0

    print(f"Reading and parsing with {args.workers} worker(s) …")
    if args.workers == 1:
        results = map(read_and_parse_patch, patches)
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=args.workers)
        results = executor.map(read_and_parse_patch, patches, chunksize=1)

    raw_file = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False,
    )
    raw_path = Path(raw_file.name)
    writer = csv.writer(raw_file)
    try:
        try:
            for version, cards in tqdm(results, total=len(patches), desc="patches"):
                if cards is None:
                    print(f"  {version}: no CardDefs.xml, skipping")
                    continue

                writer.writerows(
                    tuple("\\N" if value is None else value for value in card)
                    for card in cards
                )
                total_cards += len(cards)
                done_patches += 1
                tqdm.write(f"  {version}: {len(cards)} collectible cards")
        finally:
            if executor is not None:
                executor.shutdown(cancel_futures=True)

        raw_file.close()
        print("Bulk loading parsed cards into DuckDB …")
        escaped_path = str(raw_path).replace("'", "''")
        conn.execute(
            "COPY cards (patch_id, name, type, text, health, attack, mana, armor, "
            "card_set, class, rarity, tribe) "
            f"FROM '{escaped_path}' (FORMAT CSV, NULL '\\N')"
        )
    finally:
        if not raw_file.closed:
            raw_file.close()
        raw_path.unlink(missing_ok=True)

    print(f"\nIngested {done_patches} patches, {total_cards:,} card-rows total.")

    print("Deduplicating …")
    removed = deduplicate(conn)
    print(f"Removed {removed:,} duplicate rows ({total_cards - removed:,} remaining).")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
