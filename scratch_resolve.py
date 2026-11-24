import json
from pathlib import Path

ledger_dir = Path("holon-knowledge/ledger")
for jsonl_file in ledger_dir.glob("*.jsonl"):
    print(f"Resolving {jsonl_file}...")
    text = jsonl_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    records = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("<<<<<<<") or stripped.startswith("=======") or stripped.startswith(">>>>>>>"):
            continue
        try:
            obj = json.loads(stripped)
            key = json.dumps(obj, sort_keys=True)
            if key not in seen:
                seen.add(key)
                records.append(obj)
        except json.JSONDecodeError:
            pass

    def get_created_at(r):
        return r.get("created_at", "")

    records.sort(key=get_created_at)

    new_content = "\n".join(json.dumps(r, sort_keys=True) for r in records) + "\n"
    jsonl_file.write_text(new_content, encoding="utf-8")
    print(f"Resolved {jsonl_file} ({len(records)} records).")
