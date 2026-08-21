"""Quick live-server verification (run while uvicorn is up)."""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path: str):
    return urllib.request.urlopen(BASE + path, timeout=30).read()


def post_json(path: str, payload: dict):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=90).read())


def main() -> int:
    html = get("/").decode("utf-8")
    print("frontend index:", 'id="root"' in html, f"({len(html)} bytes)")

    demo = post_json("/api/demo/load", {"count": 5})
    bid = demo["batch_id"]
    print("demo batch:", bid, "findings:", demo["findings_count"])

    csv_text = get(f"/api/batches/{bid}/export?format=csv").decode("utf-8")
    print("csv export lines:", len(csv_text.splitlines()))
    print("  header:", csv_text.splitlines()[0])

    rows = json.loads(get(f"/api/batches/{bid}/export?format=json").decode("utf-8"))
    print("json export rows:", len(rows))

    rules = json.loads(get("/api/rules").decode("utf-8"))
    print("audit rules:", ", ".join(r["rule_id"] for r in rules["rules"]))

    graph = json.loads(get(f"/api/batches/{bid}/graph").decode("utf-8"))
    print("graph nodes:", graph["graph"]["statistics"]["total_nodes"])

    audit = json.loads(get(f"/api/batches/{bid}/audit").decode("utf-8"))
    print("audit findings:", audit["summary"]["total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
