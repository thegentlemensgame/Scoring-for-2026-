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

# --- Fix: Add Virat Kohli POTM +25 for match_74 ---
# His score should be 158pts, needs to be 183pts (+25 POTM bonus)
# Find him by checking matchBreakdowns for his name, or by score ~158

m74_scores = db.collection("matchScores").document("match_74").get().to_dict() or {}
print("match_74 all scores:")
for pid, score in sorted(m74_scores.items(), key=lambda x: -x[1] if isinstance(x[1], (int,float)) else 0):
    print(f"  {pid}: {score}")

# Also check matchBreakdowns for name lookup
mb74 = db.collection("matchBreakdowns").document("match_74").get().to_dict() or {}
print("\nmatchBreakdowns match_74 keys:", list(mb74.keys())[:10])
# Look for virat/kohli in breakdown data
for pid, data in mb74.items():
    if isinstance(data, dict):
        name = str(data.get("name","")).lower()
        if "virat" in name or "kohli" in name:
            print(f"FOUND Virat Kohli: pid={pid}, data={data}")

# Also check players collection
print("\nSearching players collection for Virat Kohli...")
for pdoc in db.collection("players").stream():
    d = pdoc.to_dict()
    name = str(d.get("name","")).lower()
    if "virat" in name or "kohli" in name:
        print(f"FOUND: id={pdoc.id}, data={d}")
        break
