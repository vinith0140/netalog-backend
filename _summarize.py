import sys, json
sys.stdout.reconfigure(encoding='utf-8')

lines = open('pipeline_results.log', encoding='utf-8').readlines()
json_lines = [l.strip() for l in lines if l.strip().startswith('{')]
records = [json.loads(l) for l in json_lines]

latest = {}
for r in records:
    latest[r['state_id']] = r

ok     = [r for r in latest.values() if 'candidates_error' not in r]
failed = [r for r in latest.values() if 'candidates_error' in r]

total_saved   = sum(r.get('candidates_saved', 0) for r in ok)
total_skipped = sum(r.get('candidates_skipped', 0) for r in ok)
total_mla     = sum(r.get('mla_tagged', 0) for r in ok)

print("States succeeded :", len(ok))
print("States failed    :", len(failed))
print("Candidates saved :", f"{total_saved:,}")
print("Already in DB    :", f"{total_skipped:,}")
print("Est. total in DB :", f"{total_saved + total_skipped:,}")
print("MLAs tagged      :", f"{total_mla:,}")
print()
print("Failed states:")
for r in failed:
    print("  [" + str(r['state_id']) + "] " + r['name'] + " -- " + r.get('candidates_error', ''))
print()
print("Per-state summary:")
for r in sorted(ok, key=lambda x: x['state_id']):
    m = ""
    if 'ministers_total' in r:
        m = "  ministers=" + str(r['ministers_matched']) + "/" + str(r['ministers_total'])
    line = ("  [" + str(r['state_id']).rjust(2) + "] "
            + r['name'].ljust(22)
            + "  saved=" + str(r.get('candidates_saved', 0)).rjust(5)
            + "  skipped=" + str(r.get('candidates_skipped', 0)).rjust(5)
            + "  mla=" + str(r.get('mla_tagged', 0)).rjust(4)
            + m)
    print(line)
