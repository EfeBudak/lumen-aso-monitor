#!/usr/bin/env python3
"""
aso_monitor.py — DIY App Store keyword-rank + competitor-metadata tracker.

Zero third-party dependencies. Uses Apple's public iTunes Search API
(the same endpoint the App Store uses). Stores history in a local SQLite
file so you can chart trends and detect competitor changes over time.

Usage
-----
  python3 aso_monitor.py            # run a full monitoring pass
  python3 aso_monitor.py discover "sobriety tracker"
                                    # list top apps + their App Store IDs
                                    # (handy for filling in COMPETITOR_IDS)

Notes
-----
* Keyword "rank" here is your app's position in the Search API results for
  a term. It is a *proxy* for store ranking, not identical to it. Tracked
  consistently it's a solid trend signal.
* Apple rate-limits this endpoint (~20 req/min). The throttle below keeps
  you well under that.
"""

import json
import math
import plistlib
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ----------------------------- CONFIG -----------------------------
# Each tracked app is a profile in APPS (defined at the end of this section).
# The keyword-rank + competitor-metadata pass and the HTML dashboard loop over
# every profile. The keyword-gap "opportunities" pipeline also covers every app
# via per-app topic vocabularies in GAP_CONFIG (see the KEYWORD GAP SCAN section).
MY_APP_ID = 6769856284  # Lumen — its sobriety keyword/competitor globals follow

KEYWORDS = [
    "sober tracker",
    "days since",
    "sobriety counter",
    "sobriety tracker",
    "relapse tracker",
    "quit drinking",
    "sober days counter",
    "streak tracker",
    "quit smoking",
    "addiction recovery",
    "sober counter",
    "days since",
    "quit tracker",
]

# Fill these in with competitor numeric App Store IDs.
# Run:  python3 aso_monitor.py discover "sobriety tracker"
# to find them quickly.
COMPETITOR_IDS = [
    672904239   ,# I Am Sober
    1445348921  ,# Days Since: Quit Habit Tracker
    1158895079  ,# Sober Time - Sobriety Counter
    566975787   ,# Nomo -  Sobriety Clocks
    1121088244  ,# DayCount
    1547099435  ,# Sunflower - Quit Any Addiction
    1485756576  ,# Reframe: Drink Less & Thrive
    1084331959  ,# Days - Sobriety Counter
    6754048419  ,# Sobo: Sober Best Friend
    1438388363  ,# Habit Tracker
    1536343358  ,# Sobriety Counter Stop Drinking
    863872931   ,# Sober: Recovery Tracker
    1667186075  ,# Sobriety Counter
    934015977   ,# Clean Day — Sobriety Counter
    6502186815  ,# SoberAI: AI Sobriety Sponsor
    6745745695  ,# Sobi: Sobriety Tracker
    642922942   ,# My Spiritual Toolkit AA Steps
    6757267709  ,# SoberQuest - Sobriety Tracker
    1448251580  ,# Sober SideKick: Quit Addiction
    6752740681  ,# LiveSober: Sobriety Tracker
    990308161   ,# Sober Me: Tools for Recovery
    1239464706  ,# Sober Today - Day Counter
    295775656   ,# 12 Steps Companion AA Big Book
]

# Storefronts to track. ASO ranking is country-specific, so track the
# markets that matter for you (US is the big one for sobriety apps).
COUNTRIES = ["us", "gb", "ie", "tr", "br", "es"]

# ---- Additional apps (core dashboard only; no opportunities scan yet) --------
# Rock Identifier — geology / rock / mineral / crystal photo-ID app.
ROCK_KEYWORDS = [
    "rock identifier",
    "rock identification",
    "mineral identifier",
    "crystal identifier",
    "gem identifier",
    "stone identifier",
    "identify rocks",
    "rock scanner",
    "gemstone identifier",
    "mineral identification",
    "crystal identification",
    "geology",
]
ROCK_COMPETITOR_IDS = [
    1546796934,  # Rock Identifier: Stone ID (category leader)
    1553800023,  # Rock & Crystal Identifier
    1608573202,  # Stone Identifier - Rock Finder
    6469999508,  # Rock ID - Stone Identifier
    6754837588,  # RockIn Rock & Mineral identify
    1531110109,  # Minerals Center
    1528275327,  # Crystalyze: Crystal Identifier
    1623138956,  # Healing Pal: Crystal Identifier
    1513813469,  # A Guide To Crystals - The CC
    6743672966,  # Rock Identifier - AI Rock ID
    6752249234,  # Rock Identifier: Gem Value
    6560107458,  # Lens Scan: Identify Anything
]

# A Star Shot — AI photo-to-art / avatar / style-transfer app.
STAR_KEYWORDS = [
    "ai photo",
    "ai art generator",
    "ai avatar",
    "ai photo editor",
    "photo to art",
    "ai portrait",
    "ai art",
    "anime ai",
    "ai headshot",
    "ai selfie",
    "art filter",
    "ai image generator",
]
STAR_COMPETITOR_IDS = [
    1191337894,  # Photoleap: AI Photo Generator
    1642969698,  # AI Photo Generator: ARTA
    1586366816,  # WOMBO Dream - AI Art Generator
    1540719743,  # Toonapp: AI Photo & Video Art
    1658822260,  # Momo: AI Photo & Video Maker
    1621278575,  # Wonder - AI Art Generator
    1580512844,  # starryai - AI Photo Generator
    1508120751,  # ToonMe: AI Cartoon Face Maker
    1436732536,  # Lensa AI: Photo Editor
    1643890882,  # Dawn AI - Avatar generator
    1669952628,  # MyMood AI: AI Photo Generator
    6444115499,  # Anime Art - AI Art Generator
]

# Before After — pre-launch (no App Store ID yet): local-first progress-photo
# body-transformation tracker with auto-aligned timelapse export. Only the
# keyword-opportunity scan runs for it until it ships; the daily rank/metadata
# pass skips profiles whose app_id is None.
BEFORE_KEYWORDS = [
    "progress photos",
    "progress pics",
    "body transformation",
    "body progress tracker",
    "body tracker",
    "gym progress",
    "transformation tracker",
    "before and after photos",
    "weight loss progress",
    "photo progress tracker",
    "body timelapse",
    "fitness photo tracker",
]
BEFORE_COMPETITOR_IDS = [
    583840813,   # Progress Body Tracker: My BMI (category leader)
    1265152738,  # Body tracker: Photo & measure
    1369905597,  # FormaTrack - Body Tracker
    877133105,   # Selfie A Day - Everyday Photo
    1537250336,  # Photo Compare - Before & After
    1547114493,  # Progress Snapshot Body Tracker
    1180244595,  # Snapsie - Take progress pictures
    6499454966,  # My Body Tracker: PhotoJourney
    6544789120,  # Progress Pic Photos: Metamorph
    6759252082,  # GainFrame: Gym Progress Photos
    6758867697,  # Body Tracker - Progress Photos
    6747368316,  # Progress Pics - Body Tracker
    6759237484,  # Morf: Body Transformation
    1131633112,  # Body Fit Progress Tracker - Photo & Measurements
]

# Tracked-app profiles. The daily pass + dashboard loop over these. "slug"
# namespaces each app's rows in the SQLite history and identifies it in the
# report selector. Lumen reuses the globals above so the keyword-gap pipeline
# (which still reads them) stays unchanged.
APPS = [
    {
        "slug": "lumen", "name": "Lumen Sobriety", "app_id": MY_APP_ID,
        "countries": COUNTRIES, "keywords": KEYWORDS,
        "competitors": COMPETITOR_IDS,
    },
    {
        "slug": "rock", "name": "Rock Identifier", "app_id": 6751188944,
        "countries": ["us", "gb", "ie"], "keywords": ROCK_KEYWORDS,
        "competitors": ROCK_COMPETITOR_IDS,
    },
    {
        "slug": "starshot", "name": "A Star Shot", "app_id": 6755907563,
        "countries": ["us", "gb", "ie"], "keywords": STAR_KEYWORDS,
        "competitors": STAR_COMPETITOR_IDS,
    },
    {
        "slug": "beforeafter", "name": "Before After", "app_id": None,
        "countries": ["us", "gb", "ie"], "keywords": BEFORE_KEYWORDS,
        "competitors": BEFORE_COMPETITOR_IDS,
    },
]

DB_PATH = Path(__file__).with_name("aso_history.db")
SEARCH_LIMIT = 200       # max Apple allows per search
THROTTLE_SECONDS = 4     # be polite to the API
# ------------------------------------------------------------------


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "aso-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def keyword_rank(term, country, app_id):
    """Return (rank, top3) where rank is app_id's 1-based position or None."""
    params = urllib.parse.urlencode({
        "term": term,
        "country": country,
        "media": "software",
        "entity": "software",
        "limit": SEARCH_LIMIT,
    })
    data = http_get(f"https://itunes.apple.com/search?{params}")
    results = data.get("results", [])
    rank = None
    for position, app in enumerate(results, start=1):
        if app.get("trackId") == app_id:
            rank = position
            break
    top3 = [a.get("trackName", "?") for a in results[:3]]
    return rank, top3


def lookup_metadata(app_ids, country):
    ids = ",".join(str(i) for i in app_ids)
    params = urllib.parse.urlencode({"id": ids, "country": country})
    data = http_get(f"https://itunes.apple.com/lookup?{params}")
    return {app["trackId"]: app for app in data.get("results", [])}


def discover(term, country="us"):
    """Print the top apps for a term with their IDs, for populating config."""
    params = urllib.parse.urlencode({
        "term": term, "country": country, "media": "software",
        "entity": "software", "limit": 25,
    })
    data = http_get(f"https://itunes.apple.com/search?{params}")
    print(f"\nTop apps for '{term}' in {country.upper()}:\n")
    for i, app in enumerate(data.get("results", []), start=1):
        print(f"{i:>2}. {app['trackId']:<12} {app.get('trackName','?')}")
    print()


# ----------------------------- STORAGE -----------------------------

def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ranks (
            checked_at TEXT, country TEXT, keyword TEXT, rank INTEGER,
            app TEXT
        );
        CREATE TABLE IF NOT EXISTS metadata (
            checked_at TEXT, country TEXT, app_id INTEGER, app_name TEXT,
            version TEXT, rating REAL, rating_count INTEGER,
            release_notes TEXT, description TEXT, app TEXT
        );
        CREATE TABLE IF NOT EXISTS opportunities (
            checked_at TEXT, country TEXT, keyword TEXT,
            lumen_rank INTEGER, floor INTEGER, field_value INTEGER,
            winnability REAL, relevance REAL, score REAL, verdict TEXT,
            demand REAL, suggest_rank INTEGER, app TEXT
        );
    """)
    # Migrate older databases that predate the demand columns.
    have = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
    if "demand" not in have:
        conn.execute("ALTER TABLE opportunities ADD COLUMN demand REAL")
    if "suggest_rank" not in have:
        conn.execute("ALTER TABLE opportunities ADD COLUMN suggest_rank INTEGER")
    # Migrate single-app databases to the multi-app schema. Every existing row
    # predates the second/third app, so it belongs to Lumen.
    for tbl in ("ranks", "metadata", "opportunities"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        if "app" not in cols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN app TEXT")
        conn.execute(f"UPDATE {tbl} SET app='lumen' WHERE app IS NULL")
    conn.commit()


def last_metadata(conn, country, app_id, app):
    row = conn.execute("""
        SELECT version, release_notes, description, app_name
        FROM metadata WHERE country=? AND app_id=? AND app=?
        ORDER BY checked_at DESC LIMIT 1
    """, (country, app_id, app)).fetchone()
    return row


# --------------------------- HTML REPORT ---------------------------
# A self-contained dashboard generated straight from the SQLite history,
# so it can diff the two most recent passes ("what changed today") without
# re-hitting the API. Open the .html file in any browser; no server needed.

import html as _html


def _esc(s):
    return _html.escape("" if s is None else str(s))


def _rank_cell_style(rank):
    """Color tier for a keyword rank cell (lower is better)."""
    if rank is None:
        return "background:#f1efe8;color:#9a988f"      # not in top 200
    if rank <= 50:
        return "background:#97c459;color:#173404"       # strong
    if rank <= 100:
        return "background:#ef9f27;color:#412402"       # mid
    return "background:#f0997b;color:#4a1b0c"           # weak


def _two_latest_passes(conn):
    rows = conn.execute(
        "SELECT DISTINCT checked_at FROM metadata ORDER BY checked_at DESC LIMIT 2"
    ).fetchall()
    cur = rows[0][0] if rows else None
    prev = rows[1][0] if len(rows) > 1 else None
    return cur, prev


def _collect_changes(conn, app, cur, prev):
    """Return (rank_changes, releases) between prev and cur passes for one app."""
    slug = app["slug"]
    rank_changes, releases = [], []
    if not prev:
        return rank_changes, releases

    # This app's own keyword-rank movements.
    for country in app["countries"]:
        for kw in app["keywords"]:
            c = conn.execute(
                "SELECT rank FROM ranks WHERE checked_at=? AND country=? AND keyword=? AND app=?",
                (cur, country, kw, slug)).fetchone()
            p = conn.execute(
                "SELECT rank FROM ranks WHERE checked_at=? AND country=? AND keyword=? AND app=?",
                (prev, country, kw, slug)).fetchone()
            if not c or not p:
                continue
            cr, pr = c[0], p[0]
            if cr == pr:
                continue
            cc = country.upper()
            if pr is None and cr is not None:
                rank_changes.append((cc, kw, f"entered at #{cr}", "up"))
            elif cr is None and pr is not None:
                rank_changes.append((cc, kw, f"dropped out (was #{pr})", "down"))
            else:
                direction = "up" if cr < pr else "down"
                rank_changes.append((cc, kw, f"#{pr} → #{cr}", direction))

    # Competitor (and own) release activity: version / title / notes / desc.
    for country in app["countries"]:
        cur_rows = conn.execute(
            "SELECT app_id, app_name, version, release_notes, description "
            "FROM metadata WHERE checked_at=? AND country=? AND app=?",
            (cur, country, slug)).fetchall()
        for app_id, name, version, notes, desc in cur_rows:
            p = conn.execute(
                "SELECT version, release_notes, description, app_name FROM metadata "
                "WHERE checked_at=? AND country=? AND app_id=? AND app=?",
                (prev, country, app_id, slug)).fetchone()
            if not p:
                continue
            pv, pn, pd, pname = p
            what = []
            detail = {}
            if pv != version:
                what.append(f"v{pv} → v{version}")
            if pname != name:
                what.append("title changed")
                detail["old_title"] = pname
                detail["new_title"] = name
            if pn != notes:
                what.append("what's-new changed")
                if (notes or "").strip():
                    detail["notes"] = notes.strip()
                    detail["version"] = version
            if pd != desc:
                what.append("description changed")
            if what:
                releases.append({
                    "cc": country.upper(), "name": name,
                    "summary": ", ".join(what), "detail": detail,
                })
    return rank_changes, releases


def _changes_html(rank_changes, releases, has_prev):
    if not has_prev:
        return ('<p class="empty">First pass recorded — no previous data to compare '
                'against yet. Run again tomorrow and changes will appear here.</p>')
    if not rank_changes and not releases:
        return '<p class="empty">No rank movements or competitor releases since the last pass.</p>'
    parts = []
    if rank_changes:
        parts.append('<div class="ch-group"><div class="ch-head">Your rank movements</div>')
        for cc, kw, txt, d in rank_changes:
            arrow = "▲" if d == "up" else "▼"
            cls = "up" if d == "up" else "down"
            parts.append(
                f'<div class="ch-row"><span class="cc">{cc}</span>'
                f'<span class="kw">{_esc(kw)}</span>'
                f'<span class="mv {cls}">{arrow} {_esc(txt)}</span></div>')
        parts.append('</div>')
    if releases:
        parts.append('<div class="ch-group"><div class="ch-head">Competitor releases</div>')
        for r in releases:
            head = (f'<span class="cc">{r["cc"]}</span>'
                    f'<span class="kw">{_esc(r["name"])}</span>'
                    f'<span class="mv rel">{_esc(r["summary"])}</span>')
            d = r["detail"]
            body = []
            if d.get("old_title"):
                body.append(
                    f'<p class="rel-title-chg">Title: “{_esc(d["old_title"])}” '
                    f'→ “{_esc(d["new_title"])}”</p>')
            if d.get("notes"):
                txt = d["notes"]
                if len(txt) > 1500:
                    txt = txt[:1500] + "…"
                body.append(
                    f'<div class="rel-label">What’s new (v{_esc(d.get("version", ""))})</div>'
                    f'<p class="rel-text">{_esc(txt)}</p>')
            if body:
                parts.append(
                    f'<details class="rel-item"><summary>{head}</summary>'
                    f'<div class="rel-detail">{"".join(body)}</div></details>')
            else:
                parts.append(f'<div class="ch-row">{head}</div>')
        parts.append('</div>')
    return "".join(parts)


def _heatmap_html(conn, app, cur):
    countries = app["countries"]
    head = "".join(f"<th>{c.upper()}</th>" for c in countries)
    rows = []
    best = None
    ranked_cells = 0
    for kw in app["keywords"]:
        cells = []
        for country in countries:
            r = conn.execute(
                "SELECT rank FROM ranks WHERE checked_at=? AND country=? AND keyword=? AND app=?",
                (cur, country, kw, app["slug"])).fetchone()
            rank = r[0] if r else None
            if rank is not None:
                ranked_cells += 1
                if best is None or rank < best[0]:
                    best = (rank, country.upper(), kw)
            label = f"#{rank}" if rank is not None else "·"
            cells.append(f'<td style="{_rank_cell_style(rank)}">{label}</td>')
        rows.append(f'<tr><th class="kw">{_esc(kw)}</th>{"".join(cells)}</tr>')
    table = (f'<table class="heat"><thead><tr><th></th>{head}</tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>')
    return table, best, ranked_cells


def _leaderboards_html(conn, app, cur):
    countries = app["countries"]
    blocks = []
    for country in countries:
        rows = conn.execute(
            "SELECT app_id, app_name, rating, rating_count FROM metadata "
            "WHERE checked_at=? AND country=? AND app=? "
            "ORDER BY rating_count DESC NULLS LAST",
            (cur, country, app["slug"])).fetchall()
        if not rows:
            continue
        mx = max((r[3] or 0) for r in rows) or 1
        items = []
        for app_id, name, rating, rc in rows:
            rc = rc or 0
            mine = (app_id == app["app_id"])
            pct = max(rc / mx * 100, 0.4)
            bar = "#378add" if mine else "#b4b2a9"
            cls = ' class="mine"' if mine else ""
            rating_txt = f"{rating:.2f}★" if rating else "—"
            items.append(
                f'<div class="lb-row"{cls}>'
                f'<div class="lb-name">{_esc(name)}{" (you)" if mine else ""}</div>'
                f'<div class="lb-bar"><div class="lb-fill" style="width:{pct:.1f}%;background:{bar}"></div></div>'
                f'<div class="lb-num">{rc:,}</div><div class="lb-rt">{rating_txt}</div></div>')
        blocks.append(
            f'<details class="lb"{" open" if country == countries[0] else ""}>'
            f'<summary>{country.upper()} — {len(rows)} apps by rating count</summary>'
            f'{"".join(items)}</details>')
    return "".join(blocks)


_VERDICT_CLASS = {
    "TARGET": "v-target", "hold": "v-hold", "watch": "v-watch",
    "off-brand": "v-skip", "low demand": "v-skip", "too strong": "v-skip",
}


def _country_blurb(rows, app_name="Lumen"):
    """Plain-English read of one market's opportunity rows (keyword, score,
    suggest_rank, lumen_rank, floor, verdict tuples)."""
    targets = [r for r in rows if r[5] == "TARGET"]
    ranked = [r for r in rows if r[3]]                     # app in top 200

    # Traction sentence: is this market warm or cold for the app?
    if ranked:
        best = min(ranked, key=lambda r: r[3])
        traction = (f"{app_name} already ranks for {len(ranked)} of these "
                    f"(best <b>#{best[3]}</b> for “{_esc(best[0])}”), so this "
                    f"market is warm — climbing is easier than breaking in cold.")
    else:
        traction = (f"{app_name} isn’t in the top 200 for any of these yet — all "
                    "upside, but you’d be entering cold.")

    # Top opportunity sentence.
    if targets:
        t = targets[0]
        dem = f"#{t[2]}" if t[2] else "—"
        if t[4] <= 5:
            floor_note = "the bottom of page 1 is wide open"
        elif t[4] <= 300:
            floor_note = f"the weakest top-10 app has only {t[4]:,} ratings"
        else:
            floor_note = f"page 1 is harder here (floor {t[4]:,} ratings)"
        where = f", and you sit at #{t[3]}" if t[3] else ", and you’re absent"
        lead = (f"Best bet: “<b>{_esc(t[0])}</b>” — demand {dem}, {floor_note}"
                f"{where}.")
        focus = ", ".join(_esc(r[0]) for r in targets[:3])
        vocab = f" Focus vocabulary: {focus}."
    else:
        lead = ("No clean targets here — the high-demand terms are already "
                "dominated or off-brand.")
        vocab = ""

    return f'<p class="op-note">{traction} {lead}{vocab}</p>'


def _opportunities_html(conn, app):
    """Top keyword opportunities per country from the latest gap scan."""
    blocks = []
    first = None
    for country in app["countries"]:
        latest = conn.execute(
            "SELECT MAX(checked_at) FROM opportunities WHERE country=? AND app=?",
            (country, app["slug"])).fetchone()[0]
        if not latest:
            continue
        rows = conn.execute(
            "SELECT keyword, score, suggest_rank, lumen_rank, floor, verdict "
            "FROM opportunities WHERE country=? AND checked_at=? AND app=? "
            "ORDER BY score DESC LIMIT 12", (country, latest, app["slug"])).fetchall()
        if not rows:
            continue
        mx = max((r[1] or 0) for r in rows) or 1
        items = []
        for kw, score, srank, lrank, floor, verdict in rows:
            pct = max((score or 0) / mx * 100, 0.4)
            dem = f"#{srank}" if srank else "—"
            you = f"#{lrank}" if lrank else "—"
            vc = _VERDICT_CLASS.get(verdict, "v-watch")
            items.append(
                f'<div class="op-row">'
                f'<div class="op-name">{_esc(kw)}</div>'
                f'<div class="op-bar"><div class="op-fill" style="width:{pct:.1f}%"></div></div>'
                f'<div class="op-sc">{score:g}</div>'
                f'<div class="op-meta">demand {dem}</div>'
                f'<div class="op-meta">pg1 {floor:,}</div>'
                f'<div class="op-meta">you {you}</div>'
                f'<div class="op-v {vc}">{_esc(verdict)}</div></div>')
        if first is None:
            first = country
        blocks.append(
            f'<details class="lb"{" open" if country == first else ""}>'
            f'<summary>{country.upper()} — top keyword targets '
            f'(scanned {_esc(latest[:10])})</summary>'
            f'{_country_blurb(rows, app["name"])}{"".join(items)}</details>')
    if not blocks:
        return ('<p class="empty">No keyword scan recorded yet — run '
                f'<code>python3 aso_monitor.py gap {app["slug"]} us</code> '
                '(or the ASO keyword scan workflow) to populate this.</p>')
    return "".join(blocks)


def _app_section_html(conn, app, cur, prev):
    """The per-app dashboard body (cards + panels), shown when its tab is active."""
    rank_changes, releases = _collect_changes(conn, app, cur, prev)
    changes = _changes_html(rank_changes, releases, prev is not None)
    heat, best, ranked_cells = _heatmap_html(conn, app, cur)
    opps = _opportunities_html(conn, app)
    boards = _leaderboards_html(conn, app, cur)

    total_cells = len(app["keywords"]) * len(app["countries"])
    best_txt = f"{best[1]} #{best[0]}" if best else "unranked"
    best_sub = best[2] if best else "no tracked term in top 200"
    rel_count = len(releases) if prev else 0

    return f"""
  <div class="cards">
    <div class="card"><div class="lab">Best rank</div><div class="big">{_esc(best_txt)}</div><div class="sub">{_esc(best_sub)}</div></div>
    <div class="card"><div class="lab">Cells ranked</div><div class="big">{ranked_cells} / {total_cells}</div><div class="sub">keyword × market positions</div></div>
    <div class="card"><div class="lab">Competitor releases</div><div class="big">{rel_count}</div><div class="sub">since last pass</div></div>
  </div>

  <h2>What changed since last pass</h2>
  <div class="panel">{changes}</div>

  <h2>Your keyword rank by market</h2>
  {heat}
  <div class="legend">
    <span><i class="sw" style="background:#97c459"></i>top 50</span>
    <span><i class="sw" style="background:#ef9f27"></i>51–100</span>
    <span><i class="sw" style="background:#f0997b"></i>101–200</span>
    <span><i class="sw" style="background:#f1efe8"></i>not in top 200</span>
  </div>

  <h2>Where to focus — keyword opportunities</h2>
  {opps}

  <h2>Competitors by market size</h2>
  {boards}"""


def write_html(conn, now):
    cur, prev = _two_latest_passes(conn)
    if not cur:
        return None

    tabs, sections = [], []
    for i, app in enumerate(APPS):
        active = " active" if i == 0 else ""
        tabs.append(
            f'<button class="tab{active}" data-app="{app["slug"]}" '
            f'onclick="showApp(\'{app["slug"]}\')">{_esc(app["name"])}</button>')
        sections.append(
            f'<section class="app-report{active}" data-app="{app["slug"]}">'
            f'{_app_section_html(conn, app, cur, prev)}\n  </section>')

    page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ASO dashboard — {_esc(cur[:10])}</title>
<style>
  :root{{--bg:#faf9f5;--card:#fff;--bd:#e7e5dd;--tx:#2c2c2a;--mut:#6b6a64;--hint:#9a988f}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
  .wrap{{max-width:880px;margin:0 auto;padding:28px 20px 60px}}
  h1{{font-size:22px;font-weight:600;margin:0 0 2px}}
  .ts{{color:var(--mut);font-size:13px;margin-bottom:22px}}
  h2{{font-size:16px;font-weight:600;margin:30px 0 10px}}
  .cards{{display:flex;gap:12px;flex-wrap:wrap}}
  .card{{flex:1;min-width:160px;background:var(--card);border:.5px solid var(--bd);border-radius:12px;padding:14px 16px}}
  .card .lab{{font-size:12px;color:var(--mut)}}
  .card .big{{font-size:23px;font-weight:600;margin-top:2px}}
  .card .sub{{font-size:12px;color:var(--hint)}}
  .panel{{background:var(--card);border:.5px solid var(--bd);border-radius:12px;padding:16px 18px;margin-top:10px}}
  .empty{{color:var(--mut);font-size:14px;margin:4px 0}}
  .ch-group{{margin-bottom:12px}}
  .ch-group:last-child{{margin-bottom:0}}
  .ch-head{{font-size:13px;font-weight:600;color:var(--mut);margin:6px 0 6px}}
  .ch-row{{display:flex;align-items:center;gap:10px;padding:4px 0;font-size:14px}}
  .ch-row .cc{{font-size:11px;font-weight:600;color:var(--mut);background:#f1efe8;border-radius:4px;padding:2px 6px;min-width:30px;text-align:center}}
  .ch-row .kw{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .mv{{font-size:13px;font-weight:500}}
  .mv.up{{color:#3b6d11}} .mv.down{{color:#a32d2d}} .mv.rel{{color:#185fa5}}
  details.rel-item{{padding:2px 0}}
  details.rel-item>summary{{cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;padding:4px 0;font-size:14px}}
  details.rel-item>summary::-webkit-details-marker{{display:none}}
  details.rel-item>summary::before{{content:"▸";color:var(--hint);font-size:10px;width:10px}}
  details.rel-item[open]>summary::before{{content:"▾"}}
  .rel-detail{{margin:2px 0 10px 38px;padding:10px 12px;background:var(--bg);border:.5px solid var(--bd);border-radius:8px}}
  .rel-title-chg{{font-size:13px;color:var(--tx);margin:0 0 6px}}
  .rel-label{{font-size:12px;font-weight:600;color:var(--mut);margin-bottom:4px}}
  .rel-text{{font-size:13px;line-height:1.5;white-space:pre-wrap;margin:0;color:var(--tx)}}
  table.heat{{border-collapse:separate;border-spacing:3px;width:100%;table-layout:fixed;font-size:12px}}
  table.heat th{{font-weight:600;color:var(--mut)}}
  table.heat th.kw{{text-align:left;font-weight:400;color:var(--tx);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  table.heat td{{text-align:center;padding:7px 0;border-radius:4px;font-weight:600}}
  .legend{{display:flex;gap:14px;margin-top:10px;font-size:11px;color:var(--mut);flex-wrap:wrap}}
  .legend span{{display:flex;align-items:center;gap:5px}}
  .sw{{width:11px;height:11px;border-radius:2px;display:inline-block}}
  details.lb{{background:var(--card);border:.5px solid var(--bd);border-radius:12px;padding:6px 16px;margin-top:8px}}
  details.lb summary{{cursor:pointer;font-size:14px;font-weight:600;padding:8px 0;list-style:none}}
  details.lb summary::-webkit-details-marker{{display:none}}
  .lb-row{{display:flex;align-items:center;gap:10px;padding:4px 0;font-size:13px}}
  .lb-row.mine .lb-name{{font-weight:600;color:#185fa5}}
  .lb-name{{width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .lb-bar{{flex:1;background:#f1efe8;border-radius:4px;height:16px}}
  .lb-fill{{height:100%;border-radius:4px}}
  .lb-num{{width:74px;text-align:right;color:var(--mut)}}
  .lb-rt{{width:46px;text-align:right;color:var(--hint)}}
  .op-note{{font-size:13px;line-height:1.55;color:var(--mut);margin:2px 0 12px;padding-bottom:10px;border-bottom:.5px solid var(--bd)}}
  .op-note b{{color:var(--tx);font-weight:600}}
  .op-row{{display:flex;align-items:center;gap:10px;padding:5px 0;font-size:13px}}
  .op-name{{width:140px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .op-bar{{flex:1;min-width:50px;background:#f1efe8;border-radius:4px;height:16px}}
  .op-fill{{height:100%;border-radius:4px;background:#1d9e75}}
  .op-sc{{width:34px;text-align:right;font-weight:600}}
  .op-meta{{width:74px;text-align:right;color:var(--mut);font-size:12px}}
  .op-v{{width:78px;text-align:center;font-size:11px;font-weight:600;border-radius:4px;padding:2px 0}}
  .v-target{{background:#e1f5ee;color:#0f6e56}}
  .v-watch{{background:#f1efe8;color:#5f5e5a}}
  .v-hold{{background:#e6f1fb;color:#0c447c}}
  .v-skip{{background:#faece7;color:#993c1d}}
  .tabs{{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 24px}}
  .tab{{font:inherit;font-size:13px;font-weight:600;color:var(--mut);background:var(--card);border:.5px solid var(--bd);border-radius:999px;padding:7px 16px;cursor:pointer}}
  .tab.active{{background:#2c2c2a;color:#fff;border-color:#2c2c2a}}
  .app-report{{display:none}}
  .app-report.active{{display:block}}
</style>
<div class="wrap">
  <h1>ASO dashboard</h1>
  <div class="ts">pass @ {_esc(cur)}{' · comparing to ' + _esc(prev) if prev else ''}</div>
  <div class="tabs">{"".join(tabs)}</div>
  {"".join(sections)}
</div>
<script>
  function showApp(slug) {{
    document.querySelectorAll('.app-report').forEach(function (s) {{
      s.classList.toggle('active', s.dataset.app === slug);
    }});
    document.querySelectorAll('.tab').forEach(function (t) {{
      t.classList.toggle('active', t.dataset.app === slug);
    }});
  }}
</script>
</html>"""

    out = DB_PATH.with_name(f"aso_report_{cur[:10]}.html")
    out.write_text(page)
    # Stable filename so GitHub Pages (or any host) always shows the latest.
    DB_PATH.with_name("index.html").write_text(page)
    return out


# ------------------------- KEYWORD GAP SCAN -------------------------
# "Which keywords should Lumen get better at to win more installs?"
# Reuses the Search API: one call per keyword returns the full ranked
# field *with* each app's rating count, so a single pass gives us where
# Lumen ranks, who owns the term, and how soft the bottom of page 1 is.

# Roots that keep a mined phrase on-topic for a sobriety app.
RELEVANT_ROOTS = {
    "sober", "sobriety", "quit", "drink", "drinking", "alcohol", "addiction",
    "recovery", "habit", "streak", "counter", "tracker", "days", "clean",
    "smoking", "sponsor", "relapse", "dry", "quitting", "stop",
}
_STOPWORDS = {
    "the", "a", "an", "app", "my", "your", "you", "are", "is", "for", "and",
    "of", "to", "free", "best", "pro", "plus", "new", "with", "any", "day",
}
# Generic modifiers that pair with a root to form a real search phrase
# (e.g. "drink less", "since quit"). Anything outside roots+modifiers is
# treated as a brand word and dropped, so competitor names like "sunflower"
# or "nomo" don't leak in as fake keyword candidates.
_GENERIC_MODIFIERS = {
    "less", "more", "stop", "daily", "since", "time", "count", "log",
    "calendar", "free", "soberity", "control",
}
# Native-language seed terms per non-English storefront. Verified against the
# live autocomplete — each returns real localized sobriety queries. Without
# these, an English-only pipeline is blind to the local-language demand pool.
LOCALIZED_SEEDS = {
    "tr": ["alkol bırakma", "sigara bırakma", "bağımlılık", "gün sayacı",
           "alışkanlık", "sayaç"],
    "br": ["parar de beber", "sobriedade", "contador de sobriedade",
           "dias sem beber", "parar de fumar", "sóbrio", "alcoolismo"],
    "es": ["dejar de beber", "sobriedad", "contador de sobriedad",
           "dejar de fumar", "sobrio", "dejar el alcohol", "racha"],
}


def _tokens(text):
    """Lowercase word tokens, Unicode-aware (keeps á, ç, ı, ş, ü ...)."""
    return [t for t in re.findall(r"[^\W\d_]+", (text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS]


# Subject anchors: words that genuinely mean "sobriety / quitting / addiction"
# in our markets' languages. A mined or auto-discovered candidate must contain
# at least one — that's what stops generic tracker-word pairs (e.g. "clean dry"
# → a laundry app, "time contador") from leaking in. Curated KEYWORDS and
# LOCALIZED_SEEDS bypass this gate; only machine-generated phrases are filtered.
STRONG_SUBJECTS = {
    # English
    "sober", "sobriety", "drink", "drinking", "drunk", "alcohol", "alcoholic",
    "addiction", "addict", "recovery", "relapse", "sponsor", "quit", "quitting",
    "smoking", "smoke", "vaping", "vape",
    # Portuguese
    "sobriedade", "sóbrio", "sobrio", "beber", "bebida", "álcool", "alcool",
    "alcoolismo", "fumar", "vício", "vicio", "recuperação",
    # Spanish
    "sobriedad", "alcohólico", "alcoholico", "adicción", "adiccion", "recuperación",
    # Turkish
    "alkol", "alkolü", "ayık", "ayıklık", "içki", "sigara", "bağımlılık", "bağımlı",
}


def build_topic_vocab(comp_meta, seed_terms, roots, modifiers, subjects):
    """On-topic word set for one storefront, in whatever language it uses.

    The app's roots/modifiers/subjects stay (English has real demand
    everywhere), plus: any word appearing in 2+ competitor app names (a topic
    word, not a one-off brand), plus every token from that market's localized
    seeds. This self-localizes so non-English candidates pass the relevance gate.
    """
    vocab = set(roots) | set(modifiers) | set(subjects)
    name_counts = Counter()
    for app in comp_meta.values():
        for t in set(_tokens(app.get("trackName", ""))):
            name_counts[t] += 1
    vocab |= {t for t, c in name_counts.items() if c >= 2}
    for s in seed_terms:
        vocab |= set(_tokens(s))
    return vocab


def search_field(term, country):
    """Full ranked Search API field for a term (each app keeps its metadata)."""
    params = urllib.parse.urlencode({
        "term": term, "country": country, "media": "software",
        "entity": "software", "limit": SEARCH_LIMIT,
    })
    data = http_get(f"https://itunes.apple.com/search?{params}")
    return data.get("results", [])


# Storefront IDs for the App Store search-hints (autocomplete) endpoint.
# Autocomplete order is Apple's own popularity ranking — our demand signal.
STOREFRONTS = {
    "us": "143441", "gb": "143444", "ie": "143449",
    "tr": "143480", "br": "143503", "es": "143454",
}
# Seed prefixes to type into autocomplete. Apple returns its most-searched
# completions for each, which both scores our candidates and surfaces new ones.
HINT_SEEDS = [
    "sober", "sobriety", "quit", "alcohol", "drinking", "drink", "recovery",
    "addiction", "habit", "streak", "clean", "days since", "stop drinking",
    "dry", "relapse", "sponsor", "counter", "tracker",
]
HINTS_URL = "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"


# ---- Per-app gap-scan vocabularies -------------------------------------------
# Each app's keyword-opportunity scan needs its own topic words so the relevance
# gate keeps machine-mined/auto-discovered phrases on-brand. "subjects" are the
# words that genuinely mean the topic (a mined candidate must contain at least
# one); "roots" is the wider on-topic set (subjects + utility words); "modifiers"
# are generic words that pair with a subject to form a real phrase. Curated
# `keywords` in the app profile bypass this gate; only mined phrases are filtered.
# NOTE: _tokens() drops tokens <=2 chars, so "ai" never survives tokenization —
# A Star Shot's other words (avatar/anime/art/portrait...) carry the subject gate.

# Rock Identifier — geology / rock / mineral / crystal / gem identification.
ROCK_SUBJECTS = {
    "rock", "rocks", "mineral", "minerals", "crystal", "crystals", "gem", "gems",
    "gemstone", "gemstones", "stone", "stones", "geology", "geode", "geodes",
    "ore", "fossil", "fossils", "quartz", "agate", "meteorite", "meteorites",
}
ROCK_MODIFIERS = {
    "identifier", "identify", "identification", "scanner", "scan", "finder",
    "find", "value", "guide", "collection", "collector",
}
ROCK_ROOTS = ROCK_SUBJECTS | ROCK_MODIFIERS | {
    "mineralogy", "gemology", "appraisal", "geologist", "specimen", "lapidary",
    "rockhound", "rockhounding",
}
ROCK_HINT_SEEDS = [
    "rock", "rock identifier", "rock id", "mineral", "mineral identifier",
    "crystal", "crystal identifier", "gem", "gemstone", "stone",
    "stone identifier", "geode", "geology", "fossil", "quartz", "rock scanner",
    "rock collection", "identify rock",
]

# A Star Shot — AI photo-to-art / avatar / style-transfer.
STAR_SUBJECTS = {
    "avatar", "avatars", "anime", "portrait", "portraits", "headshot",
    "headshots", "selfie", "selfies", "art", "artwork", "cartoon", "painting",
    "manga",
}
STAR_MODIFIERS = {
    "generator", "editor", "maker", "filter", "filters", "photo", "image",
    "face", "create", "creator", "style", "styles",
}
STAR_ROOTS = STAR_SUBJECTS | STAR_MODIFIERS | {
    "photos", "images", "picture", "pictures", "edit", "generate", "effect",
    "effects", "enhancer", "retouch", "collage", "camera", "video",
}
STAR_HINT_SEEDS = [
    "ai photo", "ai art", "ai avatar", "ai portrait", "ai headshot", "ai selfie",
    "anime", "anime ai", "art generator", "photo to art", "ai image", "cartoon",
    "ai painting", "ai photo editor", "ai art generator",
]

# Before After — progress-photo body-transformation tracking / timelapse.
BEFORE_SUBJECTS = {
    "body", "physique", "transformation", "transformations", "progress",
    "gym", "fitness", "muscle", "muscles", "weight", "bodybuilding",
    "workout", "workouts", "gains",
}
BEFORE_MODIFIERS = {
    "photo", "photos", "pic", "pics", "picture", "pictures", "tracker",
    "track", "tracking", "timelapse", "lapse", "compare", "comparison",
    "before", "after", "journal", "diary", "log", "camera", "selfie",
    "snapshot", "daily", "journey", "measure", "measurements",
}
BEFORE_ROOTS = BEFORE_SUBJECTS | BEFORE_MODIFIERS | {
    "bulking", "cutting", "lean", "gain", "loss", "shredded", "checkin",
}
BEFORE_HINT_SEEDS = [
    "progress photo", "progress photos", "progress pics", "body transformation",
    "body progress", "body tracker", "gym progress", "weight loss photo",
    "before and after", "transformation", "physique", "fitness photo",
    "photo progress", "body timelapse", "muscle growth", "selfie a day",
]

# Topic config per app slug. Apps absent here have no opportunity scan (the
# dashboard shows a placeholder for them).
GAP_CONFIG = {
    "lumen": {
        "subjects": STRONG_SUBJECTS, "roots": RELEVANT_ROOTS,
        "modifiers": _GENERIC_MODIFIERS, "hint_seeds": HINT_SEEDS,
        "localized_seeds": LOCALIZED_SEEDS,
    },
    "rock": {
        "subjects": ROCK_SUBJECTS, "roots": ROCK_ROOTS,
        "modifiers": ROCK_MODIFIERS, "hint_seeds": ROCK_HINT_SEEDS,
        "localized_seeds": {},
    },
    "starshot": {
        "subjects": STAR_SUBJECTS, "roots": STAR_ROOTS,
        "modifiers": STAR_MODIFIERS, "hint_seeds": STAR_HINT_SEEDS,
        "localized_seeds": {},
    },
    "beforeafter": {
        "subjects": BEFORE_SUBJECTS, "roots": BEFORE_ROOTS,
        "modifiers": BEFORE_MODIFIERS, "hint_seeds": BEFORE_HINT_SEEDS,
        "localized_seeds": {},
    },
}


def fetch_hints(prefix, country):
    """Apple autocomplete completions for a prefix, in popularity order."""
    sf = STOREFRONTS.get(country.lower())
    if not sf:
        return []
    url = f"{HINTS_URL}?clientApplication=Software&term={urllib.parse.quote(prefix)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "iTunes/12.11 (Macintosh; OS X 10.15.7)",
        "X-Apple-Store-Front": sf,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = plistlib.loads(resp.read())
        return [h.get("term", "").strip().lower()
                for h in data.get("hints", []) if h.get("term")]
    except Exception:
        return []


def harvest_demand(seeds, country, log=True):
    """Query autocomplete for each seed; return {term: best 0-based rank}."""
    term_rank = {}
    seen = []
    for prefix in dict.fromkeys(s.lower() for s in seeds):
        seen.append(prefix)
        for i, term in enumerate(fetch_hints(prefix, country)):
            if term not in term_rank or i < term_rank[term]:
                term_rank[term] = i
        time.sleep(THROTTLE_SECONDS)
    if log:
        print(f"  harvested {len(term_rank)} autocomplete terms "
              f"from {len(seen)} prefixes")
    return term_rank


def _hint_match(cand, hint):
    """True if an autocomplete hint represents the candidate keyword."""
    return hint == cand or hint.startswith(cand + " ") or cand.startswith(hint + " ")


def demand_for(cand, term_rank):
    """(demand 0..1, best suggest rank 1-based or None) for a candidate."""
    best = None
    for hint, rank in term_rank.items():
        if _hint_match(cand, hint) and (best is None or rank < best):
            best = rank
    if best is None:
        return 0.05, None                      # Apple never suggests it = a ghost
    return round((10 - best) / 10.0, 2), best + 1


def hint_discoveries(term_rank, known, vocab, subjects, limit=10):
    """On-topic autocomplete terms we don't already track, popularity-ordered."""
    have = {k.lower() for k in known}
    out = []
    for term, rank in sorted(term_rank.items(), key=lambda x: x[1]):
        toks = _tokens(term)
        if len(toks) < 2 or term in have:
            continue
        # Every word on-topic, and at least one a real subject for this app —
        # drops brands, generic word pairs, and cross-language junk.
        if all(t in vocab for t in toks) and any(t in subjects for t in toks):
            out.append((term, rank + 1))
        if len(out) >= limit:
            break
    return out


def mine_candidates(meta_by_id, existing, vocab, subjects, limit=15):
    """Pull on-topic 2-word phrases out of competitor app names (any language)."""
    counts = Counter()
    for app in meta_by_id.values():
        words = _tokens(app.get("trackName", ""))
        for a, b in zip(words, words[1:]):
            # Both words on-topic, at least one a real subject (drops brands and
            # generic word pairs like "clean dry").
            if a in vocab and b in vocab and (a in subjects or b in subjects):
                counts[f"{a} {b}"] += 1
    have = {k.lower() for k in existing}
    out = []
    for phrase, _ in counts.most_common():
        if phrase not in have:
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


def score_keyword(term, results, app_text, app_id, demand=0.05,
                  suggest_rank=None, vocab=None):
    """Return a dict of opportunity signals for one keyword's search field.

    The "lumen_rank" key is the tracked app's own rank (kept under that name to
    match the long-standing `opportunities.lumen_rank` column)."""
    vocab = vocab or set()
    lumen_rank = None
    for pos, app in enumerate(results[:SEARCH_LIMIT], start=1):
        if app_id is not None and app.get("trackId") == app_id:
            lumen_rank = pos
            break

    top10 = results[:10]
    counts = [int(a.get("userRatingCount") or 0) for a in top10]
    field_value = max(counts) if counts else 0          # strongest incumbent
    floor = min(counts) if counts else 0                # weakest app on page 1
    top_app = results[0].get("trackName", "?") if results else "—"

    # Winnability: how soft is the bottom of page 1? Low floor = crackable.
    winnability = 1.0 / (1.0 + floor / 500.0)
    # Value: a term strong apps fight over is worth money (kept as a modifier).
    value = min(math.log10(field_value + 10) / 6.0, 1.0)
    # Relevance: keyword words on-topic for this market (localized vocab) or
    # already present in the app's listing text. Language-correct per storefront.
    toks = _tokens(term)
    present = sum(1 for t in toks if t in vocab or t in app_text)
    relevance = 1.0 if toks and present == len(toks) else (0.5 if present else 0.2)
    # Headroom: room to climb (already top 10 = little upside).
    if lumen_rank and lumen_rank <= 10:
        headroom = 0.3
    elif lumen_rank:
        headroom = 1.0
    else:
        headroom = 0.7

    # Demand (autocomplete popularity) is now the primary multiplier; value
    # is demoted to a gentle modifier so it can't dominate a low-search term.
    score = round(demand * winnability * relevance * headroom
                  * (0.5 + 0.5 * value) * 100, 1)

    if lumen_rank and lumen_rank <= 10:
        verdict = "hold"
    elif relevance < 0.5:
        verdict = "off-brand"
    elif demand < 0.12:
        verdict = "low demand"
    elif floor > 1500:
        verdict = "too strong"
    elif floor <= 300 and demand >= 0.3:
        verdict = "TARGET"
    else:
        verdict = "watch"

    return {
        "keyword": term, "lumen_rank": lumen_rank, "floor": floor,
        "field_value": field_value, "winnability": round(winnability, 2),
        "relevance": relevance, "demand": demand, "suggest_rank": suggest_rank,
        "score": score, "verdict": verdict, "top_app": top_app,
    }


def keyword_gap(app, country="us"):
    country = country.lower()
    slug, name = app["slug"], app["name"]
    cfg = GAP_CONFIG.get(slug)
    if not cfg:
        print(f"No keyword-gap config for {name} ({slug}) — skipping.")
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # The app's own listing text drives the relevance check (prefer fresh DB).
    # Pre-launch apps have no listing yet, so their curated keywords stand in.
    if app["app_id"] is None:
        app_text = " ".join(app["keywords"]).lower()
    else:
        row = conn.execute(
            "SELECT app_name, description FROM metadata "
            "WHERE country=? AND app_id=? AND app=? "
            "ORDER BY checked_at DESC LIMIT 1",
            (country, app["app_id"], slug)).fetchone()
        if row:
            app_text = f"{row[0]} {row[1]}".lower()
        else:
            m = lookup_metadata([app["app_id"]], country).get(app["app_id"], {})
            app_text = f"{m.get('trackName','')} {m.get('description','')}".lower()

    roots, modifiers, subjects = cfg["roots"], cfg["modifiers"], cfg["subjects"]
    hint_seeds = cfg["hint_seeds"]
    comp_meta = lookup_metadata(app["competitors"], country)
    localized = cfg["localized_seeds"].get(country, [])
    vocab = build_topic_vocab(comp_meta, hint_seeds + localized,
                              roots, modifiers, subjects)

    # Candidate pool: curated keywords + this market's localized seeds +
    # phrases mined from the (localized) competitor names.
    keywords = app["keywords"]
    base = list(dict.fromkeys(
        keywords + localized
        + mine_candidates(comp_meta, keywords + localized, vocab, subjects)))

    # Demand: harvest Apple autocomplete (English + localized seeds), then let
    # it surface fresh candidates in whatever language the store uses.
    print(f"[{name}] Harvesting search demand for {country.upper()}"
          f"{' (+localized)' if localized else ''}...")
    seeds = hint_seeds + localized + [k.split()[0] for k in base]
    term_rank = harvest_demand(seeds, country)
    discovered = hint_discoveries(term_rank, base, vocab, subjects)
    candidates = list(dict.fromkeys(base + [t for t, _ in discovered]))
    if discovered:
        print("  autocomplete surfaced: "
              + ", ".join(f"{t} (#{r})" for t, r in discovered))

    print(f"\n[{name}] Scanning {len(candidates)} keywords in {country.upper()} "
          f"(~{len(candidates) * THROTTLE_SECONDS}s)...\n")

    scored = []
    for term in candidates:
        try:
            results = search_field(term, country)
        except Exception as e:
            print(f"  ! {term}: {e}")
            time.sleep(THROTTLE_SECONDS)
            continue
        demand, srank = demand_for(term, term_rank)
        s = score_keyword(term, results, app_text, app["app_id"],
                          demand, srank, vocab)
        scored.append(s)
        conn.execute(
            "INSERT INTO opportunities (checked_at, country, keyword, lumen_rank, "
            "floor, field_value, winnability, relevance, score, verdict, demand, "
            "suggest_rank, app) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, country, s["keyword"], s["lumen_rank"], s["floor"],
             s["field_value"], s["winnability"], s["relevance"], s["score"],
             s["verdict"], s["demand"], s["suggest_rank"], slug))
        time.sleep(THROTTLE_SECONDS)
    conn.commit()
    conn.close()

    scored.sort(key=lambda x: x["score"], reverse=True)

    lines = [f"KEYWORD OPPORTUNITIES — {name} — {country.upper()} @ {now}",
             "=" * 86,
             "score = demand x winnability x relevance x headroom (x value modifier)",
             "",
             f"{'keyword':<24}{'score':>6}  {'demand':>6}  {'you':>5}  "
             f"{'pg1 floor':>9}  {'verdict':<11} top app",
             "-" * 86]
    for s in scored:
        you = f"#{s['lumen_rank']}" if s["lumen_rank"] else "—"
        dem = f"#{s['suggest_rank']}" if s["suggest_rank"] else "—"
        lines.append(
            f"{s['keyword'][:23]:<24}{s['score']:>6}  {dem:>6}  {you:>5}  "
            f"{s['floor']:>9,}  {s['verdict']:<11} {s['top_app'][:24]}")
    lines += ["",
              "demand    = Apple autocomplete rank for the term (#1 = most searched, — = not suggested)",
              f"you       = {name}'s current rank for the term",
              "pg1 floor = ratings of the WEAKEST app in the top 10 (low = crackable)",
              "TARGET    = real demand + winnable + on-brand and you're not there yet"]
    report = "\n".join(lines)
    print(report)

    out = DB_PATH.with_name(f"aso_keyword_gaps_{slug}_{country}_{now[:10]}.txt")
    out.write_text(report)
    print(f"\nSaved: {out}")


def demand_report(country="us"):
    """Just the autocomplete demand landscape — what people actually search."""
    country = country.lower()
    print(f"Harvesting App Store autocomplete for {country.upper()}...\n")
    term_rank = harvest_demand(HINT_SEEDS + LOCALIZED_SEEDS.get(country, []),
                               country, log=False)
    if not term_rank:
        print("No suggestions returned (check the storefront id).")
        return
    ranked = sorted(term_rank.items(), key=lambda x: (x[1], x[0]))
    print(f"Most-searched terms Apple autocompletes ({len(ranked)} found):\n")
    for term, rank in ranked[:40]:
        print(f"  #{rank + 1:<3} {term}")


# ------------------------------ MAIN ------------------------------

def run():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    summary = [f"ASO pass @ {now}", "=" * 48]

    for profile in APPS:
        slug, name = profile["slug"], profile["name"]
        if profile["app_id"] is None:
            summary.append(f"\n\n{'#' * 10} {name} {'#' * 10}"
                           "\n(pre-launch — no listing to track yet; skipped)")
            continue
        countries, keywords = profile["countries"], profile["keywords"]
        summary.append(f"\n\n{'#' * 10} {name} {'#' * 10}")

        # 1) Keyword ranks
        summary.append("\nKEYWORD RANKS (position in Search API results)")
        for country in countries:
            summary.append(f"\n[{country.upper()}]")
            for term in keywords:
                rank, top3 = keyword_rank(term, country, profile["app_id"])
                conn.execute(
                    "INSERT INTO ranks (checked_at, country, keyword, rank, app) "
                    "VALUES (?,?,?,?,?)",
                    (now, country, term, rank, slug),
                )
                shown = f"#{rank}" if rank else "not in top 200"
                summary.append(f"  {term:<22} {shown}")
                time.sleep(THROTTLE_SECONDS)
        conn.commit()

        # 2) Competitor (and own) metadata + change detection
        watch = [profile["app_id"]] + profile["competitors"]
        summary.append("\n\nMETADATA SNAPSHOTS + CHANGES")
        for country in countries:
            meta = lookup_metadata(watch, country)
            for app_id, app in meta.items():
                version = app.get("version", "")
                notes = app.get("releaseNotes", "")
                desc = app.get("description", "")
                app_name = app.get("trackName", "")

                prev = last_metadata(conn, country, app_id, slug)
                changes = []
                if prev:
                    pv, pn, pd, pname = prev
                    if pv != version:
                        changes.append(f"version {pv} -> {version}")
                    if pname != app_name:
                        changes.append("title changed")
                    if pn != notes:
                        changes.append("what's-new changed")
                    if pd != desc:
                        changes.append("description changed")

                conn.execute(
                    "INSERT INTO metadata (checked_at, country, app_id, app_name, "
                    "version, rating, rating_count, release_notes, description, app) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (now, country, app_id, app_name, version,
                     app.get("averageUserRating"),
                     app.get("userRatingCount"), notes, desc, slug),
                )
                tag = "  *** " + ", ".join(changes) if changes else ""
                summary.append(
                    f"\n[{country.upper()}] {app_name} (v{version}) "
                    f"{app.get('averageUserRating','?')}* "
                    f"/ {app.get('userRatingCount','?')} ratings{tag}"
                )
            time.sleep(THROTTLE_SECONDS)
        conn.commit()

    report = "\n".join(summary)
    print(report)
    # Also write a dated report file you can skim later.
    out = DB_PATH.with_name(f"aso_report_{now[:10]}.txt")
    out.write_text(report)

    # And the readable dashboard.
    html_out = write_html(conn, now)
    conn.close()
    if html_out:
        print(f"\nDashboard: {html_out}")


def report_only():
    """Regenerate the HTML dashboard from existing history (no API calls)."""
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    out = write_html(conn, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    conn.close()
    if out:
        print(f"Dashboard: {out}")
    else:
        print("No history in the database yet — run a pass first.")


def _app_by_slug(slug):
    return next((p for p in APPS if p["slug"] == slug), None)


def run_gap(args):
    """Dispatch the `gap` command.

      gap                      every app, each across its own markets
      gap all [c1 c2 ...]      every app, optionally limited to those markets
      gap <slug> [country]     one app; one market or all of its markets
      gap <country>            backward-compat: Lumen for that one market
    """
    if not args or args[0] == "all":
        wanted = {c.lower() for c in args[1:]}            # empty = no filter
        for prof in APPS:
            if prof["slug"] not in GAP_CONFIG:
                continue
            for c in prof["countries"]:
                if not wanted or c in wanted:
                    keyword_gap(prof, c)
        return
    prof = _app_by_slug(args[0])
    if prof is None:                                      # `gap us` style
        keyword_gap(APPS[0], args[0])
        return
    if len(args) > 1:
        keyword_gap(prof, args[1])
    else:
        for c in prof["countries"]:
            keyword_gap(prof, c)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "discover":
        discover(sys.argv[2] if len(sys.argv) > 2 else "sobriety tracker")
    elif len(sys.argv) >= 2 and sys.argv[1] == "report":
        report_only()
    elif len(sys.argv) >= 2 and sys.argv[1] == "gap":
        run_gap(sys.argv[2:])
    elif len(sys.argv) >= 2 and sys.argv[1] == "demand":
        demand_report(sys.argv[2] if len(sys.argv) > 2 else "us")
    else:
        run()
