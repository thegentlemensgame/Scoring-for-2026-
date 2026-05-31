import os, json, base64, tempfile, firebase_admin
from firebase_admin import credentials, firestore

# FIREBASE_KEY is base64-encoded JSON (same as auto_scorer.py)
key_b64 = os.environ.get("FIREBASE_KEY")
if not key_b64:
    raise EnvironmentError("FIREBASE_KEY not set")
key_json = base64.b64decode(key_b64).decode("utf-8")
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
    tmp.write(key_json)
    tmp_name = tmp.name
cred = credentials.Certificate(tmp_name)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Fix 1: Add manual sub for Mambalam Mosquitoes GW2 (M Shahrukh Khan id=99)
config_ref = db.collection("seasonConfig").document("config")
config_data = config_ref.get().to_dict() or {}
manual_subs = config_data.get("manualSubs", {})
print("Current manualSubs:", manual_subs)
manual_subs["mosquitoes_gw2"] = [99]
config_ref.update({"manualSubs": manual_subs})
print("Fix 1 DONE: mosquitoes_gw2=[99]")

# Fix 2: Find Virat Kohli in match_74 scores and print all for identification
m74_data = db.collection("matchScores").document("match_74").get().to_dict() or {}
print("\nmatch_74 player scores:")
for pid, score in sorted(m74_data.items(), key=lambda x: -x[1] if isinstance(x[1], (int,float)) else 0)[:20]:
    print(f"  player_id={pid}: {score}")
