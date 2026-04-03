"""
espn_fetcher.py — ESPN Cricinfo API interface
Discovers completed IPL 2026 matches and fetches POTM via methods 6 & 7.
"""

import re
import time
import requests
from config import (
    ESPN_SERIES_ID, ESPN_SERIES_SLUG,
    ESPN_SCHEDULE_API, ESPN_MATCH_SUMMARY_API, ESPN_MATCH_PAGE,
    SCHEDULE, TEAM_ALIASES
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; GentlemensGame/1.0)',
    'Accept': 'application/json',
}

# ─────────────────────────────────────────────────────────────
# MATCH DISCOVERY
# ─────────────────────────────────────────────────────────────

def fetch_completed_matches():
    """
    Hit ESPN schedule API and return a list of completed matches.

    Each entry:
        {
            'match_id':   int,          # ESPN objectId
            'match_slug': str,          # e.g. "rcb-vs-srh-1st-match-1234567"
            'match_num':  int | None,   # 1-74 in our SCHEDULE
            'gw':         int | None,
            'home':       str,          # team abbr
            'away':       str,
            'status':     str,          # "result" | "in_progress" | "upcoming"
            'winner':     str | None,   # abbr of winning team
        }
    """
    url = ESPN_SCHEDULE_API.format(series_id=ESPN_SERIES_ID)
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ⚠️  ESPN schedule API failed: {e}")
        return []

    completed = []
    fixture_groups = data.get('content', {}).get('matches', []) or \
                     data.get('content', {}).get('fixtures', []) or \
                     _flatten_schedule(data)

    for match in fixture_groups:
        state = (match.get('stage') or match.get('state') or '').lower()
        is_complete = state in ('result', 'complete', 'finished', 'post')
        if not is_complete:
            # Also check via statusText
            status_text = (match.get('status', {}).get('statusText', '')
                           if isinstance(match.get('status'), dict)
                           else str(match.get('status', ''))).lower()
            if 'won' not in status_text and 'tied' not in status_text and 'abandoned' not in status_text:
                continue

        obj_id = match.get('objectId') or match.get('id') or match.get('matchId')
        slug   = match.get('slug') or match.get('matchSlug') or ''

        # Parse teams from the match entry
        teams = match.get('teams', [])
        home_abbr, away_abbr = _parse_teams(teams)

        # Detect match number
        match_num, gw = _detect_match_num(slug, home_abbr, away_abbr)

        # Detect winner
        winner = _detect_winner(match)

        if obj_id:
            completed.append({
                'match_id':   int(obj_id),
                'match_slug': slug,
                'match_num':  match_num,
                'gw':         gw,
                'home':       home_abbr,
                'away':       away_abbr,
                'status':     state or 'result',
                'winner':     winner,
            })

    print(f"  📅 ESPN API returned {len(completed)} completed match(es)")
    return completed


def _flatten_schedule(data):
    """Try multiple JSON paths ESPN might use for match list."""
    content = data.get('content', {})
    for key in ('matches', 'fixtures', 'matchList', 'fixtureList', 'rounds'):
        val = content.get(key)
        if isinstance(val, list):
            # Each element might be a round with nested matches
            out = []
            for item in val:
                if isinstance(item, dict) and 'matches' in item:
                    out.extend(item['matches'])
                elif isinstance(item, dict) and 'objectId' in item:
                    out.append(item)
            return out
    return []


def _parse_teams(teams):
    """Return (home_abbr, away_abbr) from ESPN teams array."""
    home_abbr = away_abbr = ''
    for i, t in enumerate(teams[:2]):
        team_obj = t.get('team', t)
        abbr_raw = (team_obj.get('abbreviation') or team_obj.get('shortName') or
                    team_obj.get('name', '')).lower()
        abbr = TEAM_ALIASES.get(abbr_raw, abbr_raw.upper())
        if i == 0:
            home_abbr = abbr
        else:
            away_abbr = abbr
    return home_abbr, away_abbr


def _detect_match_num(slug, home, away):
    """Return (match_num, gw) from slug or team pair."""
    if slug:
        # Method A: ordinal in slug  e.g. "rcb-vs-srh-1st-match-..."
        m = re.search(r'(\d+)(?:st|nd|rd|th)-match', slug)
        if m:
            num = int(m.group(1))
            gw = next((s[3] for s in SCHEDULE if s[0] == num), None)
            return num, gw

    # Method B: match teams from our SCHEDULE
    if home and away:
        for m_num, sch_home, sch_away, gw in SCHEDULE:
            if {home, away} == {sch_home, sch_away}:
                return m_num, gw

    return None, None


def _detect_winner(match):
    """Extract winning team abbr from match dict."""
    # Try statusText: "RCB won by 6 wickets"
    status_text = ''
    if isinstance(match.get('status'), dict):
        status_text = match['status'].get('statusText', '') or match['status'].get('result', '')
    elif isinstance(match.get('status'), str):
        status_text = match['status']
    status_text = status_text or match.get('statusText', '') or match.get('result', '')

    won = re.search(r'(\w+)\s+won\b', status_text, re.IGNORECASE)
    if won:
        raw = won.group(1).lower()
        return TEAM_ALIASES.get(raw, raw.upper())

    # Try teams array for winner flag
    for t in match.get('teams', []):
        team_obj = t.get('team', t)
        if t.get('isWinner') or t.get('winner'):
            raw = (team_obj.get('abbreviation') or team_obj.get('name', '')).lower()
            return TEAM_ALIASES.get(raw, raw.upper())

    return None


# ─────────────────────────────────────────────────────────────
# POTM — METHOD 6: ESPN match summary API
# ─────────────────────────────────────────────────────────────

def fetch_potm_from_summary_api(match_id):
    """
    POTM Method 6: hit hs-consumer-api match summary endpoint.
    Returns POTM player long name or None.
    """
    url = ESPN_MATCH_SUMMARY_API.format(
        series_id=ESPN_SERIES_ID, match_id=match_id
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    ⚠️  POTM method 6 (summary API) failed: {e}")
        return None

    # Traverse common paths
    content = data.get('content', {})
    match   = data.get('match', {})

    # content.matchPlayerAwards
    for award in content.get('matchPlayerAwards', []):
        p = award.get('player', {})
        name = p.get('longName') or p.get('name')
        if name:
            print(f"    → POTM method 6 (matchPlayerAwards): {name}")
            return name

    # content.supportInfo
    support = content.get('supportInfo', {})
    pom = support.get('playerOfTheMatch') or support.get('playersOfTheMatch')
    if pom:
        if isinstance(pom, list) and pom:
            pom = pom[0]
        if isinstance(pom, dict):
            name = pom.get('longName') or pom.get('name')
            if name:
                print(f"    → POTM method 6 (supportInfo): {name}")
                return name

    # match.playerOfTheMatch
    pom = match.get('playerOfTheMatch', [])
    if isinstance(pom, list) and pom:
        name = pom[0].get('longName') or pom[0].get('name')
        if name:
            print(f"    → POTM method 6 (match.playerOfTheMatch): {name}")
            return name
    elif isinstance(pom, dict):
        name = pom.get('longName') or pom.get('name')
        if name:
            print(f"    → POTM method 6 (match.playerOfTheMatch dict): {name}")
            return name

    # Generic scan for any 'player of the match' key
    for source in (data, content, match):
        for k, v in source.items() if isinstance(source, dict) else []:
            if 'player' in k.lower() and 'match' in k.lower():
                if isinstance(v, list) and v:
                    v = v[0]
                if isinstance(v, dict):
                    name = v.get('longName') or v.get('name')
                    if name:
                        print(f"    → POTM method 6 (key '{k}'): {name}")
                        return name

    return None


# ─────────────────────────────────────────────────────────────
# POTM — METHOD 7: Scrape ESPN match page HTML
# ─────────────────────────────────────────────────────────────

def fetch_potm_from_page_html(match_slug):
    """
    POTM Method 7: fetch full-scorecard page HTML, regex for 'Player of the Match'.
    Returns player name string or None.
    """
    url = ESPN_MATCH_PAGE.format(
        series_slug=ESPN_SERIES_SLUG, match_slug=match_slug
    )
    try:
        r = requests.get(url, headers={
            **HEADERS,
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        }, timeout=30)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"    ⚠️  POTM method 7 (page HTML) failed: {e}")
        return None

    # Pattern 1: "Player of the Match</...>Name"
    patterns = [
        r'Player of the Match[^<]*</[^>]+>\s*<[^>]+>\s*([A-Z][a-zA-Z\s\'\-\.]+)',
        r'player.of.the.match["\s:]+([A-Z][a-zA-Z\s\'\-\.]+)',
        r'"playerOfMatch"\s*:\s*"([^"]+)"',
        r'"player_of_match"\s*:\s*"([^"]+)"',
        r'Man of the Match[^<]*</[^>]+>\s*<[^>]+>\s*([A-Z][a-zA-Z\s\'\-\.]+)',
    ]

    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            # Sanity: must look like a person name (2+ words, reasonable length)
            if 5 < len(name) < 50 and len(name.split()) >= 2:
                print(f"    → POTM method 7 (HTML regex): {name}")
                return name

    # JSON-LD / embedded JSON
    json_blocks = re.findall(r'<script[^>]*type="application/json"[^>]*>([^<]+)</script>', html)
    for block in json_blocks:
        try:
            import json
            obj = json.loads(block)
            name = _deep_search_potm(obj)
            if name:
                print(f"    → POTM method 7 (embedded JSON): {name}")
                return name
        except Exception:
            pass

    return None


def _deep_search_potm(obj, depth=0):
    """Recursively search JSON blob for POTM player name."""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if 'player' in k.lower() and 'match' in k.lower():
                if isinstance(v, str) and len(v) > 3:
                    return v
                if isinstance(v, dict):
                    name = v.get('longName') or v.get('name') or v.get('fullName')
                    if name:
                        return name
            result = _deep_search_potm(v, depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _deep_search_potm(item, depth + 1)
            if result:
                return result
    return None
