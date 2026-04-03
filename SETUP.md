# IPL Auto Scorer — Setup Guide

The Gentlemen's Game 2026 · Automated nightly scoring via GitHub Actions

---

## What This Does

Every night at midnight IST (18:30 UTC), GitHub Actions automatically:

1. Hits the ESPN Cricinfo schedule API to find completed IPL matches
2. Checks Firebase to see which matches aren't yet scored
3. Downloads the full scorecard, calculates fantasy points, and writes results
4. Sends an email summary to `thegentlemensgamecirca2025@gmail.com`

The website leaderboards update in real-time as soon as Firebase is written.

---

## One-Time Setup

### Step 1 — Create a GitHub Repository

1. Go to [github.com](https://github.com) → **New repository**
2. Name it `gentlemens-game-scorer` (or anything you like)
3. Make it **Private**
4. Click **Create repository**

Upload all files from this `ipl-auto-scorer/` folder to the repo root. The structure should be:

```
.github/
  workflows/
    daily-scorer.yml
    manual-score.yml
    revert-match.yml
auto_scorer.py
config.py
espn_fetcher.py
scorer_core.py
requirements.txt
tests/
  test_scoring.py
SETUP.md
```

---

### Step 2 — Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these three secrets:

#### `FIREBASE_KEY`

This is your Firebase service account key, base64-encoded.

On Mac/Linux:
```bash
base64 -i "Firebase key.json" | tr -d '\n'
```

On Windows (PowerShell):
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("Firebase key.json"))
```

Copy the output (one long string) and paste as the secret value.

#### `GMAIL_USER`

Your Gmail address: `thegentlemensgamecirca2025@gmail.com`

#### `GMAIL_APP_PASSWORD`

A Gmail App Password (not your regular password). To create one:

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Security → 2-Step Verification (must be enabled)
3. Search for **App passwords** → Select app: **Mail** → Device: **Other** → name it `GG Scorer`
4. Copy the 16-character password and paste as the secret value

---

### Step 3 — Verify the Schedule

The workflow runs at `30 18 * * *` UTC, which is **midnight IST** (18:30 UTC = 00:00 IST).

If you want to adjust the time, edit `.github/workflows/daily-scorer.yml` and change the cron expression.

---

## Daily Operations

### Normal Operation (Zero Touch)

Once set up, everything is automatic. You'll receive an email every night like:

```
Subject: GG Scorer — 2 scored, 0 error(s)

✅ MATCHES SCORED (2):
  Match 8 (GW2): DC vs MI | POTM: Axar Patel
  Match 9 (GW2): GT vs RR | POTM: Shubman Gill

⏭️ ALREADY SCORED (skipped): Match 1, Match 2, Match 3, ...
```

---

### Monitoring Runs

View all runs at:
`https://github.com/YOUR_USERNAME/YOUR_REPO/actions`

Each run shows detailed logs including player stats, POTM detection, and Firebase write results.

---

## Manual Controls

### Trigger Scoring Manually

If a match was missed or you want to re-run:

1. Go to **Actions → Manual Score Match**
2. Click **Run workflow**
3. Paste the ESPN full-scorecard URL (e.g. `https://www.espncricinfo.com/series/ipl-2026-1510719/rcb-vs-srh-1st-match-1234567/full-scorecard`)
4. Click **Run workflow**

---

### Revert a Match Score

If you spot an error and want to wipe a match's scores from Firebase:

1. Go to **Actions → Revert Match Scores**
2. Click **Run workflow**
3. Enter the match number (e.g. `5`)
4. Click **Run workflow**

This deletes `matchScores/match_5`, `matchBreakdowns/match_5`, and `matchMeta/match_5` from Firebase.

After reverting, the website will show no score for that match. Re-score it by either:
- Waiting for the next midnight run (it'll pick it up automatically)
- Triggering **Manual Score Match** with the ESPN URL

---

### Run Tests Locally

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -v
```

---

## POTM Self-Healing

If all 7 POTM detection methods fail (rare), the match is scored without the +25 POTM bonus and flagged with `potm_pending: true` in `matchMeta/match_N`.

The next night's run automatically retries POTM for any pending matches. If resolved, it patches `matchBreakdowns` and `matchScores` with the correct +25.

You can check which matches have pending POTM in the Firebase Console:
`Firestore → matchMeta → any doc with potm_pending: true`

---

## Firebase Structure Written

```
matchScores/
  match_1/        {playerId: points, ...}
  match_2/        ...

matchBreakdowns/
  match_1/        {playerId: {runs, balls, ..., _result: {total, breakdown}}}
  match_2/        ...

matchMeta/
  match_1/        {matchNum, gw, home, away, potm, potm_pending, winner, scored_at}
  match_2/        ...

season/
  playerMapping/  {espn_to_id: {espnName: playerId}}
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Run fails with `FIREBASE_KEY not set` | Re-add the `FIREBASE_KEY` secret (check base64 encoding) |
| Run fails with `GMAIL_APP_PASSWORD` error | Regenerate the Gmail App Password and update the secret |
| `No completed matches returned` | ESPN API may be temporarily down — next night will retry |
| Match scored with wrong points | Revert the match and re-score after checking the ESPN scorecard |
| POTM never resolves | Check `matchMeta/match_N` in Firebase. If `potm_pending: true` persists for 3+ days, the match may not have a POTM awarded |
