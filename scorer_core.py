"""
scorer_core.py — The Gentlemen's Game 2026
Full scoring pipeline: fetch scorecard → extract stats → calculate points → map players.
Ported from gentlemens_game_scorer_1.py with all Colab-specific code removed.
"""

import re
import json
import requests
from collections import defaultdict
from difflib import SequenceMatcher

from cricdata import CricinfoClient

from config import (
    ESPN_SERIES_SLUG, ALL_PLAYERS, TEAM_ALIASES,
    ESPN_MATCH_SUMMARY_API, ESPN_SERIES_ID
)
from espn_fetcher import (
    fetch_potm_from_summary_api,
    fetch_potm_from_page_html,
)

ci = CricinfoClient()

# Build lookup tables from ALL_PLAYERS
ID_TO_NAME  = {str(pid): p['name'] for pid, p in ALL_PLAYERS.items()}
NAME_TO_ID  = {p['name']: str(pid) for pid, p in ALL_PLAYERS.items()}


# ─────────────────────────────────────────────────────────────
# SCORING FORMULA  (exact port of calc_fantasy_points in Colab)
# ─────────────────────────────────────────────────────────────

def calc_fantasy_points(stats):
    """
    Calculate GG fantasy points for a single player's match performance.

    Returns: (total_points: float, breakdown: dict)
    """
    pts = 0.0
    breakdown = {}

    # ── BATTING ──
    runs   = stats.get('runs', 0) or 0
    balls  = stats.get('balls', 0) or 0
    fours  = stats.get('fours', 0) or 0
    sixes  = stats.get('sixes', 0) or 0
    is_out = stats.get('isOut', False)

    if runs > 0 or balls > 0:
        base      = runs                        # 1pt per run
        pace      = runs - balls                # pace bonus (can be negative)
        four_pts  = fours * 1                   # 1pt per four
        six_pts   = sixes * 2                   # 2pts per six

        milestone = 0
        if runs >= 100:  milestone = 50
        elif runs >= 75: milestone = 30
        elif runs >= 50: milestone = 20
        elif runs >= 25: milestone = 10

        duck = -5 if (runs == 0 and balls > 0 and is_out) else 0

        bat_total = base + pace + four_pts + six_pts + milestone + duck
        pts += bat_total
        breakdown['batting'] = {
            'runs': base, 'pace': pace, 'fours': four_pts,
            'sixes': six_pts, 'milestone': milestone, 'duck': duck,
            'total': bat_total,
        }

    # ── BOWLING ──
    overs          = stats.get('overs', 0) or 0
    maidens        = stats.get('maidens', 0) or 0
    runs_conceded  = stats.get('runsConceded', 0) or 0
    wickets        = stats.get('wickets', 0) or 0
    dots           = stats.get('dots', 0) or 0

    if wickets > 0 or overs > 0:
        full_overs  = int(overs)
        part_balls  = round((overs - full_overs) * 10)
        balls_bowled = full_overs * 6 + part_balls

        wkt_pts  = wickets * 25
        economy  = runs_conceded / overs if overs > 0 else 99

        # 3-tier economy system
        if economy < 9:
            bowl_pace = round(3 * ((balls_bowled * 1.5) - runs_conceded))
        elif economy <= 12:
            bowl_pace = 0
        else:
            bowl_pace = round((balls_bowled * 2) - runs_conceded)

        dot_pts    = round(dots * 1.5, 1)
        maiden_pts = maidens * 30

        wkt_milestone = 0
        if wickets >= 5:   wkt_milestone = 50
        elif wickets >= 4: wkt_milestone = 30
        elif wickets >= 3: wkt_milestone = 20
        elif wickets >= 2: wkt_milestone = 10

        bowl_total = wkt_pts + bowl_pace + dot_pts + maiden_pts + wkt_milestone
        pts += bowl_total
        breakdown['bowling'] = {
            'wickets': wkt_pts, 'pace': bowl_pace, 'dots': dot_pts,
            'maidens': maiden_pts, 'milestone': wkt_milestone,
            'economy': round(economy, 2), 'total': bowl_total,
        }

    # ── FIELDING ──
    catches   = stats.get('catches', 0) or 0
    stumpings = stats.get('stumpings', 0) or 0
    runouts   = stats.get('runouts', 0) or 0
    field_total = (catches + stumpings + runouts) * 10
    if field_total > 0:
        pts += field_total
        breakdown['fielding'] = {
            'catches': catches * 10, 'stumpings': stumpings * 10,
            'runouts': runouts * 10, 'total': field_total,
        }

    # ── WINNING TEAM BONUS ──
    if stats.get('isWinner', False):
        pts += 5
        breakdown['win'] = 5

    # ── PLAYER OF THE MATCH ──
    if stats.get('isPOTM', False):
        pts += 25
        breakdown['potm'] = 25

    return round(pts, 1), breakdown


# ─────────────────────────────────────────────────────────────
# FUZZY NAME MATCHING  (exact port from Colab)
# ─────────────────────────────────────────────────────────────

def fuzzy_match(espn_name, our_players):
    """
    Match an ESPN player name to our player database.
    our_players: {str(pid): name}
    Returns (pid_str, confidence) or (None, 0)
    """
    espn_clean = re.sub(r'\s+', ' ', espn_name.strip())

    # Tier 1: Exact match (case-insensitive)
    for pid, name in our_players.items():
        if name.lower() == espn_clean.lower():
            return pid, 1.0

    # Tier 2: Surname + first initial
    espn_parts = espn_clean.split()
    if len(espn_parts) >= 2:
        espn_last       = espn_parts[-1].lower()
        espn_first_init = espn_parts[0][0].lower() if espn_parts[0] else ''

        candidates = []
        for pid, name in our_players.items():
            name_parts = name.split()
            if len(name_parts) >= 2:
                our_last       = name_parts[-1].lower()
                our_first_init = name_parts[0][0].lower()
                if espn_last == our_last:
                    score = 0.95 if espn_first_init == our_first_init else 0.7
                    candidates.append((pid, score))

        if len(candidates) == 1:
            return candidates[0]
        elif len(candidates) > 1:
            # Multiple surname matches — pick highest confidence
            best = max(candidates, key=lambda x: x[1])
            return best

    # Tier 3: SequenceMatcher fuzzy
    best_match = None
    best_score = 0
    for pid, name in our_players.items():
        score = SequenceMatcher(None, espn_clean.lower(), name.lower()).ratio()
        if score > best_score:
            best_score = score
            best_match = pid
    if best_score > 0.7:
        return best_match, best_score

    return None, 0


# ─────────────────────────────────────────────────────────────
# FETCH SCORECARD & EXTRACT STATS
# ─────────────────────────────────────────────────────────────

def fetch_match_stats(series_slug, match_slug, match_id=None):
    """
    Fetch scorecard from ESPN via cricdata and extract per-player stats.

    A. Batting: runs, balls, fours, sixes, isOut
    B. Bowling: overs, maidens, runsConceded, wickets, dots (with sanity cap)
    C. Fielding: catches, stumpings, runouts (from dismissal text — 2-pass)
    D. POTM: 7-method cascade (methods 1-5 in scorecard, 6-7 via espn_fetcher)
    E. Winner: parsed from statusText

    Returns:
        {
            'match_info': {...},
            'player_stats': {espn_name: stats_dict, ...},
            'potm': str | None,
            'potm_pending': bool,
            'winner': str | None,
        }
    """
    print(f"  📡 Fetching scorecard for {match_slug}...")
    scorecard  = ci.match_scorecard(series_slug, match_slug)
    match_info = ci.match_info(series_slug, match_slug)

    content      = scorecard.get('content', {})
    match_data   = scorecard.get('match', {})
    innings_list = content.get('innings', [])

    player_stats = defaultdict(lambda: {
        'runs': 0, 'balls': 0, 'fours': 0, 'sixes': 0, 'isOut': False,
        'overs': 0, 'maidens': 0, 'runsConceded': 0, 'wickets': 0, 'dots': 0,
        'catches': 0, 'stumpings': 0, 'runouts': 0, 'isPOTM': False,
        'isWinner': False, 'team': '', 'batted': False, 'bowled': False,
    })

    # ── FIRST PASS: collect all player names + teams (for fielding lookup) ──
    all_player_names = []   # [(longName, team_abbr), ...]
    seen_names = set()

    def _abbr(team_obj):
        raw = (team_obj.get('abbreviation') or team_obj.get('shortName') or
               team_obj.get('name', '')).lower()
        return TEAM_ALIASES.get(raw, raw.upper())

    for innings in innings_list:
        inn_team = _abbr(innings.get('team', {}))
        # Batters are on inn_team
        for batsman in innings.get('inningBatsmen', []):
            p    = batsman.get('player', {})
            name = p.get('longName') or p.get('name', 'Unknown')
            if name not in seen_names:
                seen_names.add(name)
                all_player_names.append((name, inn_team))
        # Bowlers are on the OTHER team
        other_teams = [
            _abbr(other.get('team', {}))
            for other in innings_list
            if _abbr(other.get('team', {})) != inn_team
        ]
        other_team = other_teams[0] if other_teams else ''
        for bowler in innings.get('inningBowlers', []):
            p    = bowler.get('player', {})
            name = p.get('longName') or p.get('name', 'Unknown')
            if name not in seen_names:
                seen_names.add(name)
                all_player_names.append((name, other_team))

    print(f"  📋 {len(all_player_names)} players detected in match")

    # ── SECOND PASS: batting, bowling, fielding ──

    for innings in innings_list:
        inn_team   = _abbr(innings.get('team', {}))
        other_teams = [
            _abbr(other.get('team', {}))
            for other in innings_list
            if _abbr(other.get('team', {})) != inn_team
        ]
        bowl_team = other_teams[0] if other_teams else ''

        # A. BATTING
        for batsman in innings.get('inningBatsmen', []):
            p    = batsman.get('player', {})
            name = p.get('longName') or p.get('name', 'Unknown')

            if batsman.get('battedType') == 'DNB':
                if not player_stats[name]['team']:
                    player_stats[name]['team'] = inn_team
                continue

            s = player_stats[name]
            s['team']   = inn_team
            s['batted'] = True
            s['runs']  += batsman.get('runs', 0) or 0
            s['balls'] += batsman.get('balls', 0) or 0
            s['fours'] += batsman.get('fours', 0) or 0
            s['sixes'] += batsman.get('sixes', 0) or 0
            if not batsman.get('isNotOut', False):
                s['isOut'] = True

        # B. BOWLING
        for bowler in innings.get('inningBowlers', []):
            p    = bowler.get('player', {})
            name = p.get('longName') or p.get('name', 'Unknown')

            s = player_stats[name]
            if not s['team']:
                s['team'] = bowl_team
            s['bowled']        = True
            s['overs']        += bowler.get('overs', 0) or 0
            s['maidens']      += bowler.get('maidens', 0) or 0
            s['runsConceded'] += bowler.get('conceded', 0) or 0
            s['wickets']      += bowler.get('wickets', 0) or 0

            # Dots — ESPN uses 'dots', '0s', or 'dot_balls'
            dots_val = bowler.get('dots') or bowler.get('0s') or bowler.get('dot_balls') or 0

            # Sanity cap: dots ≤ balls bowled
            full_ov    = int(bowler.get('overs', 0) or 0)
            part_b     = round(((bowler.get('overs', 0) or 0) - full_ov) * 10)
            balls_bwld = full_ov * 6 + part_b
            if dots_val > balls_bwld:
                print(f"    ⚠️  {name}: dots ({dots_val}) > balls ({balls_bwld}), capping")
                dots_val = balls_bwld
            s['dots'] += dots_val

        # C. FIELDING — parse dismissal texts
        for batsman in innings.get('inningBatsmen', []):
            if batsman.get('battedType') == 'DNB':
                continue
            batter_name = batsman.get('player', {}).get('longName', '')

            how_out = batsman.get('dismissalText', {})
            if isinstance(how_out, dict):
                dismissal = how_out.get('long', '') or how_out.get('short', '') or ''
            elif isinstance(how_out, str):
                dismissal = how_out
            else:
                dismissal = ''

            if not dismissal or dismissal.lower() in ('not out', 'retired hurt', 'retired not out', ''):
                continue

            print(f"      📝 {batter_name}: {dismissal}")

            # Caught & bowled
            m = re.match(r'c\s+(?:&|and)\s+b\s+(.+)', dismissal, re.IGNORECASE)
            if m:
                fielder = _find_fielder(m.group(1).strip(), inn_team, all_player_names)
                if fielder:
                    player_stats[fielder]['catches'] += 1
                    print(f"    🧤 c&b: {fielder}")
            else:
                # Caught by fielder
                m = re.match(r'c\s+(.+?)\s+b\s+', dismissal, re.IGNORECASE)
                if m:
                    fielder = _find_fielder(m.group(1).strip(), inn_team, all_player_names)
                    if fielder:
                        player_stats[fielder]['catches'] += 1
                        print(f"    🧤 catch: {fielder}")
                    else:
                        print(f"    ⚠️  UNMATCHED catch: '{m.group(1).strip()}' in '{dismissal}'")

            # Stumped
            m = re.match(r'st\s+(.+?)\s+b\s+', dismissal, re.IGNORECASE)
            if m:
                fielder = _find_fielder(m.group(1).strip(), inn_team, all_player_names)
                if fielder:
                    player_stats[fielder]['stumpings'] += 1
                    print(f"    🧤 stumping: {fielder}")

            # Run out
            m = re.search(r'run\s+out\s*\(([^)]+)\)', dismissal, re.IGNORECASE)
            if m:
                for part in re.split(r'[/,]', m.group(1)):
                    part = part.strip()
                    if part:
                        fielder = _find_fielder(part, inn_team, all_player_names)
                        if fielder:
                            player_stats[fielder]['runouts'] += 1
                            print(f"    🏃 runout: {fielder}")

    # ── D. PLAYER OF THE MATCH — 7-method cascade ──
    potm_name    = None
    potm_pending = False

    # Method 1: match_info player_awards
    awards = match_info.get('player_awards', [])
    for award in awards:
        atype = (award.get('type', '') or '').upper()
        aname = (award.get('name', '') or '').lower()
        if 'PLAYER' in atype or 'MATCH' in atype or 'match' in aname or 'man of' in aname:
            p = award.get('player', {})
            potm_name = p.get('longName') or p.get('name')
            if potm_name:
                print(f"    → POTM method 1 (player_awards): {potm_name}")
                break

    # Method 2: match_data.playerOfTheMatch
    if not potm_name:
        pom = match_data.get('playerOfTheMatch', [])
        if isinstance(pom, list) and pom:
            potm_name = pom[0].get('longName') or pom[0].get('name')
            if potm_name:
                print(f"    → POTM method 2 (match.playerOfTheMatch): {potm_name}")
        elif isinstance(pom, dict):
            potm_name = pom.get('longName') or pom.get('name')
            if potm_name:
                print(f"    → POTM method 2 (match.playerOfTheMatch dict): {potm_name}")

    # Method 3: scan all match_data keys for player+match combo
    if not potm_name:
        for key, val in match_data.items():
            if 'player' in key.lower() and 'match' in key.lower():
                if isinstance(val, list) and val:
                    potm_name = val[0].get('longName') or val[0].get('name', '')
                elif isinstance(val, dict):
                    potm_name = val.get('longName') or val.get('name', '')
                if potm_name:
                    print(f"    → POTM method 3 (match_data['{key}']): {potm_name}")
                    break

    # Method 4: content.matchPlayerAwards
    if not potm_name:
        for award in content.get('matchPlayerAwards', []):
            p = award.get('player', {})
            potm_name = p.get('longName') or p.get('name')
            if potm_name:
                print(f"    → POTM method 4 (content.matchPlayerAwards): {potm_name}")
                break

    # Method 5: content.supportInfo
    if not potm_name:
        support = content.get('supportInfo', {})
        pom = support.get('playerOfTheMatch') or support.get('playersOfTheMatch')
        if isinstance(pom, list) and pom:
            pom = pom[0]
        if isinstance(pom, dict):
            potm_name = pom.get('longName') or pom.get('name')
            if potm_name:
                print(f"    → POTM method 5 (supportInfo): {potm_name}")

    # Method 6: ESPN match summary API
    if not potm_name and match_id:
        potm_name = fetch_potm_from_summary_api(match_id)

    # Method 7: scrape match page HTML
    if not potm_name and match_slug:
        potm_name = fetch_potm_from_page_html(match_slug)

    # Apply POTM to player_stats
    potm_resolved = None
    if potm_name:
        # Exact name match
        if potm_name in player_stats:
            player_stats[potm_name]['isPOTM'] = True
            potm_resolved = potm_name
            print(f"  🏆 POTM: {potm_name}")
        else:
            # Surname fallback
            potm_surname = potm_name.split()[-1].lower()
            for pname in list(player_stats.keys()):
                if potm_surname in pname.lower():
                    player_stats[pname]['isPOTM'] = True
                    potm_resolved = pname
                    print(f"  🏆 POTM (surname match): {pname}")
                    break
            if not potm_resolved:
                print(f"  ⚠️  POTM '{potm_name}' could not be matched. Will retry next run.")
                potm_pending = True
    else:
        print(f"  ⚠️  All 7 POTM methods failed. Will score without POTM and retry next run.")
        potm_pending = True

    # ── E. WINNING TEAM BONUS ──
    status_text = (match_data.get('statusText', '') or '').lower()
    winner_abbr = None
    m = re.search(r'(\w+)\s+won\b', status_text)
    if m:
        raw = m.group(1).lower()
        winner_abbr = TEAM_ALIASES.get(raw, m.group(1).upper())
        print(f"  🏆 Winner: {winner_abbr}")

    if winner_abbr:
        wcount = 0
        for pname, pstats in player_stats.items():
            if pstats.get('team', '').upper() == winner_abbr:
                pstats['isWinner'] = True
                wcount += 1
        print(f"  ✅ {wcount} players get +5 winning bonus")

    # Teams
    teams = match_data.get('teams', [])
    home  = _abbr(teams[0].get('team', {})) if len(teams) > 0 else ''
    away  = _abbr(teams[1].get('team', {})) if len(teams) > 1 else ''

    return {
        'match_info': {
            'home':   home,
            'away':   away,
            'title':  match_data.get('title', ''),
            'status': match_data.get('statusText', ''),
            'date':   match_data.get('startDate', ''),
        },
        'player_stats': dict(player_stats),
        'potm':         potm_resolved,
        'potm_pending': potm_pending,
        'winner':       winner_abbr,
    }


def _find_fielder(name_text, batting_team, all_player_names):
    """
    Match a fielder name snippet to a player in this match.
    Handles: full name, surname only, †keeper prefix, multi-word names.
    Fielder CANNOT be on the batting team.
    """
    clean       = re.sub(r'[†()\[\]]', '', name_text).strip()
    if not clean:
        return None
    clean_lower = clean.lower()
    clean_words = clean_lower.split()

    candidates = []

    for pname, pteam in all_player_names:
        if pteam == batting_team:
            continue  # fielder is never on batting team
        pname_lower = pname.lower()
        pname_words = pname_lower.split()

        # Exact full name
        if clean_lower == pname_lower:
            return pname

        # All words from the fielder text appear in the player name
        if all(w in pname_words for w in clean_words):
            candidates.append((pname, 3))
            continue

        # Surname match (last word)
        if clean_words[-1] == pname_words[-1]:
            candidates.append((pname, 2))
            continue

        # Any single word match
        if any(w in pname_words for w in clean_words):
            candidates.append((pname, 1))

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x[1])
    top_conf    = candidates[0][1]
    top_matches = [c for c in candidates if c[1] == top_conf]

    return top_matches[0][0]  # Return best (or first at equal confidence)


# ─────────────────────────────────────────────────────────────
# MAP PLAYERS + CALCULATE POINTS
# ─────────────────────────────────────────────────────────────

def score_match(fetch_result, espn_to_id_cache=None):
    """
    Map ESPN names → our player IDs and compute fantasy points.

    fetch_result: return value from fetch_match_stats()
    espn_to_id_cache: dict {espn_name: str(pid)} — accumulated across runs (from Firebase)

    Returns:
        {
            'scores':    {str(pid): {..., 'points': float, 'breakdown': dict}},
            'unmatched': [espn_name, ...],
            'espn_cache': updated espn_to_id_cache,
        }
    """
    if espn_to_id_cache is None:
        espn_to_id_cache = {}

    scores    = {}
    unmatched = []

    for espn_name, stats in fetch_result['player_stats'].items():
        # Check accumulated mapping cache
        pid = espn_to_id_cache.get(espn_name)

        if not pid:
            pid, conf = fuzzy_match(espn_name, ID_TO_NAME)
            if pid and conf > 0.7:
                espn_to_id_cache[espn_name] = pid
                our_name = ID_TO_NAME.get(pid, '?')
                print(f"    ✅ Mapped: {espn_name} → {our_name} (id:{pid}, {conf:.0%})")
            else:
                pid = None

        if pid is None:
            unmatched.append(espn_name)
            continue

        pts, breakdown = calc_fantasy_points(stats)
        scores[pid] = {
            'espnName':      espn_name,
            'team':          stats['team'],
            'runs':          stats.get('runs', 0),
            'ballsFaced':    stats.get('balls', 0),
            'fours':         stats.get('fours', 0),
            'sixes':         stats.get('sixes', 0),
            'isOut':         stats.get('isOut', False),
            'oversBowled':   stats.get('overs', 0),
            'maidens':       stats.get('maidens', 0),
            'runsConceded':  stats.get('runsConceded', 0),
            'wickets':       stats.get('wickets', 0),
            'dots':          stats.get('dots', 0),
            'catches':       stats.get('catches', 0),
            'stumpings':     stats.get('stumpings', 0),
            'runouts':       stats.get('runouts', 0),
            'isMoM':         stats.get('isPOTM', False),
            'isWinner':      stats.get('isWinner', False),
            'points':        pts,
            'breakdown':     breakdown,
        }

    print(f"\n  📊 Scored {len(scores)} players, {len(unmatched)} unmatched")
    if unmatched:
        print(f"  ⚠️  Unmatched: {', '.join(unmatched)}")

    # Top scorers
    top = sorted(scores.items(), key=lambda x: x[1]['points'], reverse=True)[:8]
    print(f"\n  🏆 TOP SCORERS:")
    for pid, d in top:
        details = []
        if d['runs'] > 0:       details.append(f"{d['runs']}r({d['ballsFaced']}b)")
        if d['wickets'] > 0:    details.append(f"{d['wickets']}w")
        if d['catches'] > 0:    details.append(f"{d['catches']}ct")
        if d.get('isMoM'):      details.append("🏆POTM")
        if d.get('isWinner'):   details.append("+5WIN")
        print(f"      {d['points']:>6}pts  {d['espnName']:<25} {' '.join(details)}")

    return {
        'scores':     scores,
        'unmatched':  unmatched,
        'espn_cache': espn_to_id_cache,
    }
