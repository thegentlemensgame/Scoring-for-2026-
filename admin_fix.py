import os, json, base64, tempfile, firebase_admin
from firebase_admin import credentials, firestore

key_b64 = os.environ.get("FIREBASE_KEY")
key_json = base64.b64decode(key_b64).decode("utf-8")
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
    tmp.write(key_json)
    tmp_name = tmp.name
cred = credentials.Certificate(tmp_name)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Fix: Add Virat Kohli POTM +25 bonus for match_74
# player_id=21, current score=158.0, new score=183.0
VIRAT_KOHLI_ID = 21
POTM_BONUS = 25

m74_ref = db.collection("matchScores").document("match_74")
m74_data = m74_ref.get().to_dict() or {}
current_score = m74_data.get(VIRAT_KOHLI_ID, m74_data.get(str(VIRAT_KOHLI_ID)))
print(f"Virat Kohli (id={VIRAT_KOHLI_ID}) current score: {current_score}")

# Update score: 158.0 -> 183.0
# Try both int and string key
update_dict = {}
if VIRAT_KOHLI_ID in m74_data:
    new_score = m74_data[VIRAT_KOHLI_ID] + POTM_BONUS
    update_dict[VIRAT_KOHLI_ID] = new_score
elif str(VIRAT_KOHLI_ID) in m74_data:
    new_score = m74_data[str(VIRAT_KOHLI_ID)] + POTM_BONUS
    update_dict[str(VIRAT_KOHLI_ID)] = new_score
else:
    print("ERROR: player 21 not found in match_74 scores")
    print("Available keys:", list(m74_data.keys())[:10])
    exit(1)

m74_ref.update(update_dict)
print(f"DONE: Updated player {VIRAT_KOHLI_ID} score: {current_score} -> {new_score}")

# Also set potmPlayerId in matchMeta/match_74 if that field exists
meta_ref = db.collection("matchMeta").document("match_74")
meta_data = meta_ref.get().to_dict() or {}
print(f"matchMeta/match_74 keys: {list(meta_data.keys())}")
if "potmPlayerId" in meta_data or "potm" in str(meta_data).lower():
    meta_ref.set({"potmPlayerId": VIRAT_KOHLI_ID}, merge=True)
    print(f"Set potmPlayerId={VIRAT_KOHLI_ID} in matchMeta/match_74")
