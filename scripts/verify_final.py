"""Final boot check: rules list + frontend serving (run with server up)."""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"

rules = json.loads(urllib.request.urlopen(BASE + "/api/rules", timeout=5).read())
print(f"rules ({len(rules['rules'])}):", ", ".join(r["rule_id"] for r in rules["rules"]))

html = urllib.request.urlopen(BASE + "/", timeout=5).read().decode("utf-8")
print("frontend served:", 'id="root"' in html)

health = json.loads(urllib.request.urlopen(BASE + "/api/health", timeout=5).read())
print("health:", health["status"])
return 0 if 'id="root"' in html and health["status"] == "ok" else 1
