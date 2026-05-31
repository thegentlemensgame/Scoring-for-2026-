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

# List all documents in seasonConfig to find the right one
print("seasonConfig documents:")
for doc in db.collection("seasonConfig").stream():
    print(f"  id={doc.id}, keys={list(doc.to_dict().keys())[:8]}")

# Try to find the config doc with manualSubs
found_doc = None
for doc in db.collection("seasonConfig").stream():
    d = doc.to_dict()
    if "manualSubs" in d or "currentGW" in d or "schedule" in d:
        found_doc = doc.id
        print(f"Found config doc: {doc.id}")
        break

# If no doc found, try common IDs
if not found_doc:
    for doc_id in ["config", "season", "2026", "main", "settings"]:
        d = db.collection("seasonConfig").document(doc_id).get().to_dict()
        if d:
            found_doc = doc_id
            print(f"Found at ID: {doc_id}, keys: {list(d.keys())[:8]}")
            break

if found_doc:
    # Add manual sub
    ref = db.collection("seasonConfig").document(found_doc)
    existing = ref.get().to_dict() or {}
    manual_subs = existing.get("manualSubs", {})
    manual_subs["mosquitoes_gw2"] = [99]
    ref.set({"manualSubs": manual_subs}, merge=True)
    print(f"DONE: set manualSubs.mosquitoes_gw2=[99] in {found_doc}")
else:
    print("ERROR: no seasonConfig document found")

# Also print match_74 scores
m74_data = db.collection("matchScores").document("match_74").get().to_dict() or {}
print("\nmatch_74 top scores:")
for pid, score in sorted(m74_data.items(), key=lambda x: -x[1] if isinstance(x[1], (int,float)) else 0)[:15]:
    print(f"  player_id={pid}: {score}")
