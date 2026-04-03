"""
auto_scorer.py — The Gentlemen's Game 2026
Main pipeline: discover → diff → score → write → log → email.
Runs every midnight IST via GitHub Actions (cron 0 18 * * * UTC).
Can also be triggered manually for a specific match URL.
"""

import os
import sys
import json
import base64
import tempfile
import argparse
import traceback
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import firebase_admin
from firebase_admin import credentials, firestore

from config import (
    FIREBASE_PROJECT_ID, NOTIFY_EMAIL,
    ESPN_SERIES_SLUG, SCHEDULE, MATCH_NUM_TO_GW
)
from espn_fetcher import fetch_completed_matches
from scorer_core import fetch_match_stats, score_match


# ─────────────────────────────────────────────────────────────
# FIREBASE INIT
# ─────────────────────────────────────────────────────────────

def init_firebase():
    """
    Initialise Firebase from FIREBASE_KEY env var (base64-encoded JSON).
    GitHub Actions stores the raw JSON as a secret; we base64-encode it.
    """
    if firebase_admin._apps:
        return firestore.client()

    key_b64 = os.environ.get('FIREBASE_KEY')
    if not key_b64:
        raise EnvironmentError("FIREBASE_KEY environment variable is not set.")

    # Decode and write to a temp file
    key_json = base64.b64decode(key_b64).decode('utf-8')
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    tmp.write(key_json)
    tmp.flush()
    tmp.close()

    cred = credentials.Certificate(tmp.name)
    firebase_admin.initialize_app(cred, {'projectId': FIREBASE_PROJECT_ID})
    return firestore.client()


# ─────────────────────────────────────────────────────────────
# FIREBASE HELPERS
# ─────────────────────────────────────────────────────────────

def get_scored_match_nums(db):
    """Return set of match numbers already scored in Firebase."""
    scored = set()
    try:
        for doc in db.collection('matchScores').stream():
            num = doc.id.replace('match_', '')
            if num.isdigit():
                scored.add(int(num))
    except Exception as e:
        print(f"  ⚠️  Could not read matchScores: {e}")
    return scored


def get_potm_pending_matches(db):
    """Return list of match_nums where potm_pending == True."""
    pending = []
    try:
        for doc in db.collection('matchMeta').stream():
            data = doc.to_dict() or {}
            if data.get('potm_pending'):
                num = doc.id.replace('match_', '')
                if num.isdigit():
                    pending.append(int(num))
    except Exception:
        pass
    return pending


def get_espn_cache(db):
    """Load accumulated ESPN name → player ID mapping from Firebase."""
    try:
        doc = db.collection('season').document('playerMapping').get()
        if doc.exists:
            return doc.to_dict().get('espn_to_id', {})
    except Exception:
        pass
    return {}


def save_espn_cache(db, cache):
    """Persist updated ESPN name → player ID mapping to Firebase."""
    try:
        db.collection('season').document('playerMapping').set(
            {'espn_to_id': cache}, merge=True
        )
    except Exception as e:
        print(f"  ⚠️  Could not save ESPN cache: {e}")


def write_match_to_firebase(db, match_num, scores, potm_pending, fetch_result):
    """
    Write matchScores/match_N and matchBreakdowns/match_N to Firebase.
    Mirrors the exact structure the existing HTML reads.
    """
    # matchScores/match_N  →  {pid: points, ...}
    score_data = {pid: s['points'] for pid, s in scores.items()}
    db.collection('matchScores').document(f'match_{match_num}').set(score_data)

    # matchBreakdowns/match_N  →  {pid: {raw stats + _result}, ...}
    breakdown_data = {}
    for pid, s in scores.items():
        breakdown_data[pid] = {
            'runs':          s.get('runs', 0),
            'ballsFaced':    s.get('ballsFaced', 0),
            'fours':         s.get('fours', 0),
            'sixes':         s.get('sixes', 0),
            'isOut':         s.get('isOut', False),
            'oversBowled':   s.get('oversBowled', 0),
            'maidens':       s.get('maidens', 0),
            'runsConceded':  s.get('runsConceded', 0),
            'wickets':       s.get('wickets', 0),
            'dots':          s.get('dots', 0),
            'catches':       s.get('catches', 0),
            'stumpings':     s.get('stumpings', 0),
            'runouts':       s.get('runouts', 0),
            'isMoM':         s.get('isMoM', False),
            'isWinner':      s.get('isWinner', False),
            '_result':       {'total': s['points'], 'breakdown': s['breakdown']},
        }
    db.collection('matchBreakdowns').document(f'match_{match_num}').set(breakdown_data)

    # matchMeta/match_N  →  flags, timestamps, match info
    meta = {
        'matchNum':     match_num,
        'gw':           MATCH_NUM_TO_GW.get(match_num),
        'home':         fetch_result['match_info'].get('home', ''),
        'away':         fetch_result['match_info'].get('away', ''),
        'status':       fetch_result['match_info'].get('status', ''),
        'potm':         fetch_result.get('potm'),
        'potm_pending': potm_pending,
        'winner':       fetch_result.get('winner'),
        'scored_at':    datetime.now(timezone.utc).isoformat(),
    }
    db.collection('matchMeta').document(f'match_{match_num}').set(meta)

    print(f"  ✅ Firebase updated: matchScores/match_{match_num} + matchBreakdowns/match_{match_num}")


def retry_potm_for_match(db, match_num, match_id, match_slug, espn_cache):
    """
    Retry POTM detection for a previously potm_pending match.
    If found, updates matchBreakdowns and matchScores with the extra +25.
    """
    from espn_fetcher import fetch_potm_from_summary_api, fetch_potm_from_page_html
    from scorer_core import fuzzy_match, ID_TO_NAME, calc_fantasy_points

    print(f"\n  🔁 Retrying POTM for Match {match_num}...")

    potm_name = fetch_potm_from_summary_api(match_id) or fetch_potm_from_page_html(match_slug)
    if not potm_name:
        print(f"  ⏳ POTM still not found for Match {match_num}")
        return False

    # Find player ID
    pid, conf = fuzzy_match(potm_name, ID_TO_NAME)
    if not pid or conf < 0.7:
        print(f"  ⚠️  POTM '{potm_name}' could not be mapped to a player ID")
        return False

    print(f"  🏆 POTM resolved: {potm_name} (id:{pid})")

    # Update matchBreakdowns: add isMoM=True and bump _result
    bd_ref = db.collection('matchBreakdowns').document(f'match_{match_num}')
    bd_doc = bd_ref.get()
    if bd_doc.exists:
        bd = bd_doc.to_dict()
        if pid in bd:
            bd[pid]['isMoM'] = True
            # Recalculate points with POTM
            raw = bd[pid]
            stats = {
                'runs': raw.get('runs', 0), 'balls': raw.get('ballsFaced', 0),
                'fours': raw.get('fours', 0), 'sixes': raw.get('sixes', 0),
                'isOut': raw.get('isOut', False),
                'overs': raw.get('oversBowled', 0), 'maidens': raw.get('maidens', 0),
                'runsConceded': raw.get('runsConceded', 0), 'wickets': raw.get('wickets', 0),
                'dots': raw.get('dots', 0),
                'catches': raw.get('catches', 0), 'stumpings': raw.get('stumpings', 0),
                'runouts': raw.get('runouts', 0),
                'isPOTM': True, 'isWinner': raw.get('isWinner', False),
            }
            new_pts, new_breakdown = calc_fantasy_points(stats)
            bd[pid]['_result'] = {'total': new_pts, 'breakdown': new_breakdown}
            bd_ref.set(bd)

            # Update matchScores too
            db.collection('matchScores').document(f'match_{match_num}').set(
                {pid: new_pts}, merge=True
            )
        else:
            print(f"  ⚠️  Player id {pid} not found in matchBreakdowns/match_{match_num}")

    # Clear potm_pending flag
    db.collection('matchMeta').document(f'match_{match_num}').set(
        {'potm_pending': False, 'potm': potm_name, 'potm_retry_at': datetime.now(timezone.utc).isoformat()},
        merge=True
    )

    print(f"  ✅ POTM retry complete for Match {match_num}")
    return True


# ─────────────────────────────────────────────────────────────
# EMAIL NOTIFICATION
# ─────────────────────────────────────────────────────────────

def send_email(subject, body, to=NOTIFY_EMAIL):
    """Send notification email via Gmail SMTP."""
    gmail_user = os.environ.get('GMAIL_USER')
    gmail_pass = os.environ.get('GMAIL_APP_PASSWORD')

    if not gmail_user or not gmail_pass:
        print(f"  ⚠️  Email skipped (GMAIL_USER / GMAIL_APP_PASSWORD not set)")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = gmail_user
    msg['To']      = to
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to, msg.as_string())
        print(f"  📧 Email sent: {subject}")
    except Exception as e:
        print(f"  ⚠️  Email failed: {e}")


def build_email_body(processed, skipped, potm_retried, errors):
    """Build plain-text email body for the run summary."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = [
        f"The Gentlemen's Game — Auto Scorer Run",
        f"Run time: {now}",
        f"{'='*50}",
        '',
    ]

    if processed:
        lines.append(f"✅ MATCHES SCORED ({len(processed)}):")
        for m in processed:
            potm_note = f" | POTM: {m['potm']}" if m.get('potm') else ' | POTM: pending'
            lines.append(f"  Match {m['match_num']} (GW{m['gw']}): "
                         f"{m['home']} vs {m['away']}{potm_note}")
        lines.append('')

    if potm_retried:
        lines.append(f"🔁 POTM RETRIES RESOLVED ({len(potm_retried)}):")
        for m in potm_retried:
            lines.append(f"  Match {m['match_num']}: {m['potm']}")
        lines.append('')

    if skipped:
        lines.append(f"⏭️  ALREADY SCORED (skipped): {', '.join(f'Match {n}' for n in skipped)}")
        lines.append('')

    if errors:
        lines.append(f"❌ ERRORS ({len(errors)}):")
        for e in errors:
            lines.append(f"  Match {e['match_num']}: {e['error']}")
        lines.append('')

    lines.append('─'*50)
    lines.append('View the leaderboard: https://thegentlemensgame2026.web.app')
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# CORE PIPELINE
# ─────────────────────────────────────────────────────────────

def run_daily_pipeline(db):
    """
    Main nightly pipeline:
    1. Discover completed ESPN matches
    2. Diff against what's already in Firebase
    3. Score new matches and write to Firebase
    4. Retry POTM for pending matches
    5. Send email summary
    """
    print(f"\n{'='*60}")
    print(f"🏏 THE GENTLEMEN'S GAME — AUTO SCORER")
    print(f"{'='*60}\n")

    scored_nums  = get_scored_match_nums(db)
    print(f"  Firebase already has: {sorted(scored_nums) or 'none'}")

    espn_matches = fetch_completed_matches()
    if not espn_matches:
        print("  No completed matches returned from ESPN API.")
        send_email(
            "GG Auto Scorer — No matches found",
            "ESPN API returned no completed matches. Check the schedule."
        )
        return

    espn_cache = get_espn_cache(db)

    processed    = []
    skipped      = []
    errors       = []
    potm_retried = []

    for m in espn_matches:
        match_num = m['match_num']
        if match_num is None:
            print(f"  ⚠️  Could not determine match number for {m['match_slug']} — skipping")
            continue

        if match_num in scored_nums:
            skipped.append(match_num)
            print(f"  ⏭️  Match {match_num} already scored — skipping")
            continue

        gw = m['gw'] or MATCH_NUM_TO_GW.get(match_num)
        print(f"\n{'─'*50}")
        print(f"  Processing Match {match_num} — GW{gw}: {m['home']} vs {m['away']}")

        try:
            fetch_result = fetch_match_stats(
                series_slug=ESPN_SERIES_SLUG,
                match_slug=m['match_slug'],
                match_id=m['match_id'],
            )

            score_result = score_match(fetch_result, espn_cache)
            espn_cache   = score_result['espn_cache']

            write_match_to_firebase(
                db, match_num,
                score_result['scores'],
                fetch_result['potm_pending'],
                fetch_result,
            )
            save_espn_cache(db, espn_cache)

            processed.append({
                'match_num': match_num,
                'gw':        gw,
                'home':      m['home'],
                'away':      m['away'],
                'potm':      fetch_result.get('potm'),
            })

        except Exception as e:
            tb = traceback.format_exc()
            print(f"  ❌ Error processing Match {match_num}: {e}\n{tb}")
            errors.append({'match_num': match_num, 'error': str(e)})

    # Retry POTM for pending matches
    pending_nums = get_potm_pending_matches(db)
    if pending_nums:
        print(f"\n  🔁 Retrying POTM for {len(pending_nums)} pending match(es): {pending_nums}")
        # Build slug lookup from ESPN matches
        slug_map = {m['match_num']: m for m in espn_matches if m['match_num']}
        for pnum in pending_nums:
            entry = slug_map.get(pnum)
            if not entry:
                print(f"  ⚠️  No ESPN data for pending Match {pnum}")
                continue
            success = retry_potm_for_match(
                db, pnum, entry['match_id'], entry['match_slug'], espn_cache
            )
            if success:
                potm_retried.append({'match_num': pnum, 'potm': '(resolved)'})

    # Email summary
    subject = f"GG Scorer — {len(processed)} scored, {len(errors)} error(s)"
    body    = build_email_body(processed, skipped, potm_retried, errors)
    send_email(subject, body)

    print(f"\n{'='*60}")
    print(f"  Run complete. {len(processed)} new, {len(skipped)} skipped, {len(errors)} errors.")
    print(f"{'='*60}\n")

    return len(errors) == 0


def run_single_match(db, match_url):
    """
    Manual trigger: score a single match from its ESPN URL.
    URL format: https://www.espncricinfo.com/series/{series_slug}/{match_slug}/...
    """
    import re
    from config import SCHEDULE, MATCH_NUM_TO_GW, TEAM_ALIASES

    m = re.search(r'/series/([^/]+)/([^/]+)', match_url)
    if not m:
        raise ValueError(f"Cannot parse series/match slugs from URL: {match_url}")

    series_slug = m.group(1)
    match_slug  = m.group(2)

    # Detect match number
    num_m = re.search(r'(\d+)(?:st|nd|rd|th)-match', match_slug)
    match_num = int(num_m.group(1)) if num_m else None

    if not match_num:
        # Try teams from slug
        slug_lower  = match_slug.lower().replace('-', ' ')
        found_teams = []
        for alias, code in sorted(TEAM_ALIASES.items(), key=lambda x: -len(x[0])):
            if alias in slug_lower and code not in found_teams:
                found_teams.append(code)
        if len(found_teams) >= 2:
            for m_num, home, away, gw in SCHEDULE:
                if set(found_teams[:2]) == {home, away}:
                    match_num = m_num
                    break

    if not match_num:
        raise ValueError(f"Could not determine match number from slug: {match_slug}")

    gw = MATCH_NUM_TO_GW.get(match_num)
    print(f"\n  Manual score: Match {match_num} (GW{gw}) — {match_slug}")

    espn_cache   = get_espn_cache(db)
    fetch_result = fetch_match_stats(series_slug, match_slug)
    score_result = score_match(fetch_result, espn_cache)

    save_espn_cache(db, score_result['espn_cache'])
    write_match_to_firebase(
        db, match_num,
        score_result['scores'],
        fetch_result['potm_pending'],
        fetch_result,
    )

    print(f"\n  ✅ Match {match_num} manually scored and saved.")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='GG Auto Scorer')
    parser.add_argument(
        '--match-url', type=str, default=None,
        help='Score a specific match by URL (manual mode)'
    )
    args = parser.parse_args()

    db = init_firebase()

    if args.match_url:
        run_single_match(db, args.match_url)
    else:
        success = run_daily_pipeline(db)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
