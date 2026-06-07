import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from app.database import get_db
db = get_db()

for const in ["ANDOLE", "PALAIR"]:
    r = db.table("politicians").select("name,constituency,position").ilike("constituency", f"%{const}%").eq("state_id", 24).execute()
    print(f"=== {const} ({len(r.data)} rows) ===")
    for p in r.data:
        print(f"  [{p['position']}] {p['name']}  ({p['constituency']})")

# Azharuddin - search all states, no constituency
r2 = db.table("politicians").select("name,constituency,position,state_id").ilike("name", "%azharuddin%").execute()
print(f"\n=== azharuddin ({len(r2.data)} rows) ===")
for p in r2.data:
    print(f"  {p}")

# Also try Damodar
r3 = db.table("politicians").select("name,constituency,position").ilike("name", "%damodar%").eq("state_id", 24).execute()
print(f"\n=== damodar ({len(r3.data)} rows) ===")
for p in r3.data:
    print(f"  [{p['position']}] {p['name']}  ({p['constituency']})")
