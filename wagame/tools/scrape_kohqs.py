"""Scrape kohqs.com/wa/heroes into wagame/data/heroes.json.

Run from the project root on a host with internet access:

    python -m wagame.tools.scrape_kohqs

By default this writes to wagame/data/heroes.json (overwriting it). Use --dry
to print to stdout without writing.

The page is server-rendered HTML; we slice it into hero blocks by the
`class="row rowwa rowdetail"` marker and pull each field with focused regexes
against stable hooks (image filenames, anchor URLs, `<b>` tag, abilities div).
Stdlib-only, no third-party deps. When the site's DOM changes, adjust the
regexes in `_parse_hero_block` and `_extract_bonuses`.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

URL = "https://kohqs.com/wa/heroes"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "heroes.json"

log = logging.getLogger("scrape_kohqs")


def fetch_html(url: str = URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} fetching {url}: {exc.reason}") from exc


# -- parsing ----------------------------------------------------------------

_BLOCK_MARKER = 'class="row rowwa rowdetail"'

_CODENAME_RE = re.compile(r'/wa/heroes/([a-z0-9_]+)"')
_NAME_RE = re.compile(r"<b\s*>\s*([^<]+?)\s*</b>")
_RARITY_RE = re.compile(r"hero_rarity_([a-z]+)\.png")
_BIOME_RE = re.compile(r"biome_([a-z]+)_\d+\.png")
_DATE_RE = re.compile(r"color:#b9bbbe[^>]*>\s*([^<]+?)\s*<", re.IGNORECASE)
_TYPE_RE = re.compile(
    r'<span class="smaller[^"]*"[^>]*>\s*([a-zA-Z]+)\s*</span>'
)
_ABILITIES_RE = re.compile(
    r'class="abilities[^"]*"[^>]*>(.+?)</div>\s*</a>',
    re.DOTALL,
)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _split_hero_blocks(html: str) -> list[str]:
    positions = [m.start() for m in re.finditer(re.escape(_BLOCK_MARKER), html)]
    if not positions:
        return []
    blocks: list[str] = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(html)
        blocks.append(html[start:end])
    return blocks


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    head = raw.split("(", 1)[0].strip()
    m = re.match(r"([A-Za-z]{3})\s+'?(\d{2,4})", head)
    if not m:
        return head or None
    month = _MONTHS.get(m.group(1).lower())
    year = m.group(2)
    if len(year) == 2:
        year = "20" + year
    return f"{year}-{month}" if month else head


def _extract_bonuses(block: str) -> list[str]:
    m = _ABILITIES_RE.search(block)
    if not m:
        return []
    chunk = m.group(1)
    # Drop the "DETAILS >" sub-div.
    chunk = re.sub(
        r'<div class="rowlink[^"]*"[^>]*>.+?</div>',
        "",
        chunk,
        flags=re.DOTALL,
    )
    bonuses: list[str] = []
    for part in _BR_RE.split(chunk):
        text = html_lib.unescape(_TAG_RE.sub("", part)).strip()
        if not text:
            continue
        if text.lower() == "details":
            continue
        bonuses.append(text)
    return bonuses


_KNOWN_RARITIES = {"common", "rare", "epic", "legendary", "mythic"}


def _parse_hero_block(block: str) -> dict[str, Any] | None:
    codename_m = _CODENAME_RE.search(block)
    name_m = _NAME_RE.search(block)
    if not codename_m or not name_m:
        return None
    codename = codename_m.group(1).lower()
    name = html_lib.unescape(name_m.group(1).strip())

    rarity_m = _RARITY_RE.search(block)
    biome_m = _BIOME_RE.search(block)
    date_m = _DATE_RE.search(block)

    rarity = rarity_m.group(1).lower() if rarity_m else "rare"
    biome = biome_m.group(1).lower() if biome_m else None
    release = _normalize_date(date_m.group(1)) if date_m else None

    # The "type" pill is the only pure-text smaller span (rarity/biome spans
    # start with an <img>, the date span has digits/punctuation).
    htype: str | None = None
    for candidate in _TYPE_RE.findall(block):
        cand = candidate.strip().lower()
        if cand and cand not in _KNOWN_RARITIES and cand != biome:
            htype = cand
            break

    bonuses = _extract_bonuses(block)

    return {
        "codename": codename,
        "name": name,
        "rarity": rarity,
        "element": None,
        "house": None,
        "terrain": biome,
        "release_date": release,
        "bonuses": bonuses,
        "tags": [htype] if htype else [],
    }


def extract_heroes(html: str) -> list[dict[str, Any]]:
    heroes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _split_hero_blocks(html):
        hero = _parse_hero_block(block)
        if hero is None:
            continue
        if hero["codename"] in seen:
            continue
        seen.add(hero["codename"])
        heroes.append(hero)
    return heroes


# -- cli --------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=URL, help="Source URL.")
    parser.add_argument("--out", default=str(OUTPUT), help="Output JSON path.")
    parser.add_argument("--dry", action="store_true", help="Print to stdout, don't write.")
    parser.add_argument("--raw", action="store_true", help="Dump raw HTML and exit.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    html = fetch_html(args.url)
    if args.raw:
        sys.stdout.write(html)
        return

    heroes = extract_heroes(html)
    log.info("Extracted %d hero card(s) from %s", len(heroes), args.url)

    if not heroes:
        raise SystemExit(
            "No heroes parsed. The page DOM has likely changed — rerun with "
            "--raw and adjust regexes in _parse_hero_block / _extract_bonuses."
        )

    payload = {
        "_meta": {
            "version": 1,
            "source": args.url,
            "note": "Generated by wagame.tools.scrape_kohqs. Edit by hand if needed.",
        },
        "heroes": heroes,
    }
    output = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.dry:
        sys.stdout.write(output)
    else:
        Path(args.out).write_text(output, encoding="utf-8")
        log.info("Wrote %d heroes to %s", len(heroes), args.out)


if __name__ == "__main__":
    main()
