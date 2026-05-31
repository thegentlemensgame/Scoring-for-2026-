import os, json, firebase_admin
from firebase_admin import credentials, firestore

cred_dict = json.loads(os.environ["FIREBASE_CREDENTIALS"])
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Fix 1: Add manual sub for Mambalam Mosquitoes GW2 (M Shahrukh Khan id=99)
config_ref = db.collection("seasonConfig").document("config")
config_doc = config_ref.get()
config_data = config_doc.to_dict() or {}
manual_subs = config_data.get("manualSubs", {})
print("Current manualSubs:", manual_subs)
manual_subs["mosquitoes_gw2"] = [99]
config_ref.update({"manualSubs": manual_subs})
print("Fix 1 DONE: mosquitoes_gw2=[99]")

# Fix 2: Find Virat Kohli in match_74 and add +25 POTM bonus
m74_ref = db.collection("matchScores").document("match_74")
m74_data = m74_ref.get().to_dict() or {}
# Find Virat Kohli - try player id 18 (common ID from IPL fantasy)
# Print all player scores to find the right ID
print("match_74 scores:")
for pid, score in sorted(m74_data.items(), key=lambda x: -x[1] if isinstance(x[1], (int,float)) else 0):
    print(f"  {pid}: {score}")

# Also check matchBreakdowns for Virat Kohli name
mb74 = db.collection("matchBreakdowns").document("match_74").get().to_dict() or {}
print("\nBreakdown keys:", list(mb74.keys())[:10])
