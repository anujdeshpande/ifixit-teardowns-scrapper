"""Extract chip / IC part numbers from iFixit teardowns.

2026 rewrite of the original 2015 scraper:
  - Python 3, type hints, argparse, requests with a polite session.
  - Uses the iFixit JSON API (api/2.0/guides/<id>) instead of scraping HTML
    with BeautifulSoup. The API returns each teardown as steps -> lines, where
    every line carries `text_raw`, a `bullet` colour and a nesting `level`.
  - Teardown enumeration: the API has no working "list teardowns" filter, so we
    grab guide IDs off the public /Teardown listing page with a single regex.
  - IC extraction is still regex-based (no LLM yet), but much broader than the
    old "ALLCAPS token not preceded by 'the'" heuristic.

Run:
    python3 teardown_parts.py --limit 5
    python3 teardown_parts.py --guide 67382 --guide 152903
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict

import requests

API = "https://www.ifixit.com/api/2.0"
LISTING_URL = "https://www.ifixit.com/Teardown"
USER_AGENT = "ifixit-teardown-parts/2026 (research; +https://github.com/)"

# --- Identifying iFixit-authored teardowns ---------------------------------
# The API exposes no clean "is iFixit staff" flag, so we combine three signals:
#   1. Membership in the official iFixit team (team 1), fetched live.
#   2. A small curated list of known teardown engineers the team list misses
#      (Taylor Dixon and Tobias Isakeit have no team set in the API).
#   3. A reputation gate that sits in the wide gap between iFixit authors
#      (lowest seen ~72k) and the most prolific community author (~9k) — this
#      auto-catches new staff we haven't curated yet.
IFIXIT_TEAM_ID = 1
EXTRA_IFIXIT_AUTHORS = {
    2567860: "Taylor Dixon",
    828031: "Tobias Isakeit",
}
DEFAULT_MIN_REPUTATION = 30000

# Colours iFixit uses for component-identification bullets. `black` is prose and
# `icon_note` is an aside, but chips occasionally hide in black bullets too, so
# we scan those as well and lean on the part regex + manufacturer context to
# separate signal from noise. (See README issues #1 and #2.)
COMPONENT_BULLETS = {
    "red", "orange", "yellow", "green",
    "light_blue", "blue", "violet", "purple",
}

# Known semiconductor / module vendors. Presence of one of these in a line both
# boosts confidence and lets us attribute the part to a manufacturer.
MANUFACTURERS = [
    "Apple", "Qualcomm", "Samsung", "Skyworks", "Avago", "Broadcom",
    "Texas Instruments", "TI", "STMicroelectronics", "ST Micro", "NXP",
    "Cirrus Logic", "Cirrus", "Maxim", "Analog Devices", "Bosch",
    "InvenSense", "Murata", "Toshiba", "SK Hynix", "Hynix", "Micron",
    "Intel", "Dialog", "IDT", "Lattice", "Cypress", "Nordic", "Espressif",
    "Renesas", "ON Semiconductor", "Infineon", "Microchip", "Atmel",
    "Winbond", "Macronix", "Realtek", "Marvell", "MediaTek", "Sony",
    "Bosch Sensortec", "Knowles", "AKM", "Goodix", "Synaptics", "Parade",
    "Lattice Semiconductor", "Vishay", "Diodes", "Semtech", "Silicon Labs",
    "Nuvoton", "Kioxia", "SanDisk", "Western Digital", "Wolfson", "Quanta",
    "Airoha", "Bestechnic", "BES", "Actions", "Bluetrum", "Beken", "Jieli",
    "GigaDevice", "Pericom", "Richtek", "uPI", "Anpec", "ETA Solutions",
    "ETA", "Prisemi", "Qorvo", "DSP Group", "Wacom", "RDA Microelectronics",
    "RDA", "Seiko Instruments", "Seiko", "AMS", "Bosch Sensortec",
    "AKM Semiconductor",
]
# Longest-first so "Texas Instruments" wins over a stray "TI" substring.
_MFR_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in sorted(MANUFACTURERS, key=len, reverse=True)) + r")\b"
)

# A part-number candidate: starts with an uppercase letter, contains at least
# one digit somewhere, body is uppercase-alnum, and may carry hyphen/slash
# separated suffixes (SKY78100-20, AFEM-8065, K3RG1G10CM-YGCH, MDM9645M, A10).
# The "starts uppercase AND contains a digit" rule is what kills the spec noise
# (GB, MP, IPS, 802.11, 1334, 4.2) that the old all-caps regex swept up.
PART_RE = re.compile(
    r"\b(?=[A-Z0-9][A-Z0-9/-]*[0-9])"   # must contain a digit
    r"[A-Z][A-Z0-9]*"                    # uppercase-alnum head, starts with a letter
    r"(?:[-/][A-Z0-9]+)*"                # optional hyphen/slash suffixes
    r"\b"
)

# Tokens that match PART_RE but are specs / ratings / interfaces, not chips.
NOISE_TOKENS = {
    "USB2", "USB3", "HDMI2", "DDR3", "DDR4", "DDR5",
    "LPDDR3", "LPDDR4", "LPDDR5", "H264", "H265", "MP4",
    "4K", "8K", "1080P", "720P", "5G", "4G", "3G",
    "A2DP",            # Bluetooth audio profile
}
# Common unit words (used to drop "<number> UNIT" pairs that slip through).
_UNIT_RE = re.compile(r"^(GB|MB|TB|KB|MP|MAH|MHZ|GHZ|KHZ|MM|CM|NM|PPI|HZ|V|W|MW|MAH)$", re.I)
# Patterns for non-chip tokens: ingress-protection ratings (IP67, IPX4) and
# iFixit store SKUs (IF145-020) that leak through product links.
_RATING_RE = re.compile(r"^IPX?\d{1,2}$")
_IFIXIT_SKU_RE = re.compile(r"^IF\d")
# Memory *technology* names (DDR2, DDR3L, GDDR5, LPDDR4X) — categories, not MPNs.
_MEMORY_TYPE_RE = re.compile(r"^(?:LP|G)?DDR\d[A-Z]*$")
# Screwdriver bit sizes from disassembly steps (T10 Torx, PH0 Phillips, P2
# Pentalobe). Only treated as noise when UNattributed — keeps real vendor chips
# like "Apple T2" / "Apple H1".
_SCREW_RE = re.compile(r"^(?:T|PH|PL|P)\d{1,2}$")
# Descriptive phrases that leak in as hyperlink labels: device specs and
# cross-links to other product pages ("1 GB LP DDR2 Green Memory", '15" MacBook
# Pro', "1.4 GHz Tegra 3"). A real MPN has none of these. NOTE: starting with a
# digit is NOT a reject — real chips do (1610A1, 24AA128, 10M08).
_UNIT_WORD_RE = re.compile(r"\b(GB|MB|TB|KB|MP|GHz|MHz|kHz|mAh|Wh|ppi|dpi|fps|nm|mm|cm)\b", re.I)
_SPEC_WORD_RE = re.compile(
    r"\b(Memory|RAM|display|camera|battery|storage|resolution|pixels?|processor|"
    r"lithium|MacBook|iPhone|iPad|iPod|Retina|inch|aperture|zoom)\b", re.I)


def is_noise_token(value: str, mfr: str | None) -> bool:
    if value in NOISE_TOKENS or _UNIT_RE.match(value):
        return True
    if _RATING_RE.match(value) or _IFIXIT_SKU_RE.match(value) or _MEMORY_TYPE_RE.match(value):
        return True
    if mfr is None and _SCREW_RE.match(value):
        return True
    # Descriptive spec/device phrases, not part numbers.
    if '"' in value or len(value.split()) >= 4:
        return True
    if _UNIT_WORD_RE.search(value) or _SPEC_WORD_RE.search(value):
        return True
    return False

# Strip iFixit wiki markup, KEEPING the visible link LABEL (the part number is
# very often the label, not the URL). Links come in two shapes:
#   [link|http://url|Airoha AB 1562|new_window=true]   (4 segments)
#   [https://url|MDM9645M|new_window=true]             (older, 3 segments)
# We split the inner content on '|' and keep the segment(s) that are neither the
# literal "link", nor a URL, nor a key=value attribute (e.g. new_window=true).
_LINK_RE = re.compile(r"\[([^\]]*)\]")
_BOLD_ITALIC_RE = re.compile(r"'''?")


def _link_label(m: re.Match) -> str:
    segments = [seg.strip() for seg in m.group(1).split("|")]
    labels = [
        seg for seg in segments
        if seg
        and seg.lower() != "link"
        and not re.match(r"https?://|www\.", seg)
        and "=" not in seg
    ]
    return " ".join(labels)


def strip_markup(text: str) -> str:
    text = _LINK_RE.sub(_link_label, text)
    text = _BOLD_ITALIC_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Part:
    guideid: int
    title: str
    bullet: str
    part_number: str
    manufacturer: str | None
    confidence: str            # high | medium | low
    context: str               # the cleaned bullet text it came from


def _link_labels(raw: str) -> list[str]:
    """The visible label of each wiki link in the raw text."""
    return [_link_label(m) for m in _LINK_RE.finditer(raw)]


def _looks_like_part(s: str) -> bool:
    """A part-number-ish token: has a digit and at least one uppercase letter."""
    return bool(re.search(r"\d", s)) and bool(re.search(r"[A-Z]", s))


# Max chars a vendor name may sit before a part and still be its maker. Keeps
# attribution local so a bullet listing several vendors' chips labels each one
# correctly instead of stamping the line's first vendor on all of them.
_MFR_PROXIMITY = 40


def extract_parts_from_line(text: str, bullet: str, guide: dict) -> list[Part]:
    clean = strip_markup(text)
    # Every vendor mention with its position, so we can attribute by proximity.
    mfr_spans = [(m.start(), m.end(), m.group(1)) for m in _MFR_RE.finditer(clean)]

    def nearest_mfr(pos: int) -> str | None:
        """Closest vendor whose name ends just before `pos` (within the window)."""
        best, best_end = None, -1
        for mstart, mend, name in mfr_spans:
            if best_end < mend <= pos and (pos - mend) <= _MFR_PROXIMITY:
                best, best_end = name, mend
        return best

    out: list[Part] = []
    seen: set[str] = set()

    def emit(value: str, pos: int, forced_high: bool) -> None:
        value = value.strip()
        # A vendor at the head of the value (e.g. label "Airoha AB 1562") owns
        # the rest; otherwise attribute to the nearest preceding vendor.
        lead = _MFR_RE.match(value)
        if lead:
            mfr = lead.group(1)
            value = value[lead.end():].strip()
        else:
            mfr = nearest_mfr(pos)
        if is_noise_token(value, mfr):
            return
        key = value.upper().replace(" ", "")
        if not value or key in seen:
            return
        seen.add(key)
        conf = "high" if (forced_high or mfr) else ("medium" if bullet in COMPONENT_BULLETS else "low")
        out.append(Part(
            guideid=guide["guideid"], title=guide["title"], bullet=bullet,
            part_number=value, manufacturer=mfr, confidence=conf, context=clean,
        ))

    # Signal 1 (vendor-agnostic, strongest): a hyperlinked label in a component
    # bullet is almost always the chip — captured whole, so space-separated part
    # numbers like "Airoha AB 1562" survive without any vendor list.
    if bullet in COMPONENT_BULLETS:
        for label in _link_labels(text):
            # Keep only the part-ish head (drop a trailing prose clause).
            head = re.split(r"\s+(?:Bluetooth|audio|linear|power|charger|controller|SoC|IC|chip)\b", label, maxsplit=1)[0]
            if _looks_like_part(head) and len(head) <= 40:
                pos = clean.find(head)
                emit(head, pos if pos >= 0 else 0, forced_high=True)

    # Signal 2 (vendor-agnostic): solid part-number tokens anywhere in the line.
    for m in PART_RE.finditer(clean):
        token = m.group(0)
        if len(token) < 3:           # A1, M1 etc are too ambiguous on their own
            continue
        emit(token, m.start(), forced_high=False)   # emit() applies is_noise_token

    return out


def parse_guide(guide: dict) -> list[Part]:
    parts: list[Part] = []
    for step in guide.get("steps", []):
        for line in step.get("lines", []):
            bullet = line.get("bullet", "")
            if bullet == "icon_note":
                continue
            text = line.get("text_raw") or ""
            if text:
                parts.extend(extract_parts_from_line(text, bullet, guide))
    return parts


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def list_teardown_ids(session: requests.Session, limit: int) -> list[int]:
    """Grab teardown guide IDs off the public listing page.

    The /Teardown page is JS-driven (infinite scroll), so this only sees the
    first batch (~20). Good enough for staged testing; paginating the full set
    is future work.
    """
    resp = session.get(LISTING_URL, timeout=30)
    resp.raise_for_status()
    ids: list[int] = []
    seen: set[int] = set()
    for m in re.finditer(r"/Teardown/[^\"']+/(\d+)", resp.text):
        gid = int(m.group(1))
        if gid in seen:
            continue
        seen.add(gid)
        ids.append(gid)
        if len(ids) >= limit:
            break
    return ids


def fetch_guide(session: requests.Session, guideid: int) -> dict:
    resp = session.get(f"{API}/guides/{guideid}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_team_member_ids(session: requests.Session, team_id: int) -> set[int]:
    """All userids in an iFixit team (paged)."""
    ids: set[int] = set()
    offset = 0
    while True:
        resp = session.get(f"{API}/teams/{team_id}", params={"limit": 200, "offset": offset}, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        ids.update(m["userid"] for m in batch)
        if len(batch) < 200:
            break
        offset += 200
    return ids


def is_ifixit_author(author: dict, official_ids: set[int], min_reputation: int) -> bool:
    uid = author.get("userid")
    if uid in official_ids or uid in EXTRA_IFIXIT_AUTHORS:
        return True
    return (author.get("reputation") or 0) >= min_reputation


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract IC part numbers from iFixit teardowns.")
    ap.add_argument("--limit", type=int, default=5, help="How many iFixit teardowns to process (default 5). Ignored with --guide/--ids-file.")
    ap.add_argument("--guide", type=int, action="append", help="Specific guide ID(s) to process; repeatable.")
    ap.add_argument("--ids-file", help="JSON file containing a list of guide IDs to process (all of them).")
    ap.add_argument("--out", default="parts.json", help="Output JSON file (default parts.json).")
    ap.add_argument("--delay", type=float, default=0.5, help="Seconds to sleep between API calls.")
    ap.add_argument("--quiet", action="store_true", help="Don't print a line per community-skipped guide (useful for big runs).")
    ap.add_argument("--all-authors", action="store_true", help="Don't filter to iFixit authors; include community teardowns.")
    ap.add_argument("--min-reputation", type=int, default=DEFAULT_MIN_REPUTATION,
                    help=f"Reputation gate for treating an author as iFixit staff (default {DEFAULT_MIN_REPUTATION}).")
    args = ap.parse_args(argv)

    session = make_session()

    official_ids: set[int] = set()
    if not args.all_authors:
        official_ids = fetch_team_member_ids(session, IFIXIT_TEAM_ID)
        print(f"iFixit-author filter on: team {IFIXIT_TEAM_ID} ({len(official_ids)} members) "
              f"+ {len(EXTRA_IFIXIT_AUTHORS)} curated + reputation >= {args.min_reputation}")

    # Explicit ID lists (--guide / --ids-file) are processed in full; only the
    # live listing mode is capped by --limit.
    explicit = bool(args.guide or args.ids_file)
    if args.ids_file:
        with open(args.ids_file) as f:
            candidates = json.load(f)
    elif args.guide:
        candidates = args.guide
    else:
        candidates = list_teardown_ids(session, args.limit if args.all_authors else 50)

    all_parts: list[Part] = []
    kept = community = nonteardown = errored = 0
    for i, gid in enumerate(candidates, 1):
        if not explicit and kept >= args.limit:
            break
        try:
            guide = fetch_guide(session, gid)
        except requests.HTTPError as e:
            errored += 1
            print(f"  ! guide {gid}: {e}", file=sys.stderr)
            continue
        time.sleep(args.delay)
        if guide.get("type") != "teardown":
            nonteardown += 1
            continue
        author = guide.get("author") or {}
        if not args.all_authors and not is_ifixit_author(author, official_ids, args.min_reputation):
            community += 1
            if not args.quiet:
                print(f"  - {gid:>7}  community: {author.get('username')} (rep {author.get('reputation')})  {guide['title'][:40]}")
            continue
        parts = parse_guide(guide)
        all_parts.extend(parts)
        kept += 1
        prefix = f"[{i}/{len(candidates)}] " if explicit else ""
        print(f"  + {prefix}{gid:>7}  {str(author.get('username'))[:18]:<18} {guide['title'][:36]:<36} {len(parts):>3} parts")

    with open(args.out, "w") as f:
        json.dump([asdict(p) for p in all_parts], f, indent=2)

    print(f"\n=== {len(all_parts)} parts across {kept} iFixit teardowns "
          f"(skipped {community} community, {nonteardown} non-teardown, {errored} errors) -> {args.out} ===")

    # The actual deliverable: which chip shows up in the most teardowns.
    by_part: dict[str, dict] = {}
    for p in all_parts:
        key = (f"{p.manufacturer} " if p.manufacturer else "") + p.part_number
        rec = by_part.setdefault(key, {"guides": set(), "conf": p.confidence})
        rec["guides"].add(p.guideid)
    ranked = sorted(by_part.items(), key=lambda kv: -len(kv[1]["guides"]))
    print(f"\n--- top chips by number of teardowns using them ({len(by_part)} distinct) ---")
    for name, rec in ranked[:30]:
        n = len(rec["guides"])
        if n < 2:
            break
        print(f"  {n:>3} teardowns  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
