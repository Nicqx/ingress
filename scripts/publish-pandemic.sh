#!/usr/bin/env bash
# Replace only the Pandemic gateway BasicAuth with the backend invite gate.
# Preview by default. No other application's ingress is modified.
set -euo pipefail
cd "$(dirname "$0")/.."
KUBE=(sudo k3s kubectl)
NS=default

if [[ "${1:-}" != "--apply" ]]; then
  cat <<'EOF'
Pandemic meghívólinkes belépés – ELŐNÉZET. Semmi sem változott.
Előbb frissítsd a Pandemic appot és készítsd el a két meghívólinket.
A script a podon belül három 401-es védelmi próbát végez, mielőtt
az alkalmazás önálló belépésére kapcsol át.
Aktiválás: bash scripts/publish-pandemic.sh --apply
EOF
  exit 0
fi

"${KUBE[@]}" rollout status deployment/pandemic -n "$NS" --timeout=90s
SERVICE_PORT=$("${KUBE[@]}" get service/pandemic-service -n "$NS" -o jsonpath='{.spec.ports[0].port}')
[[ "$SERVICE_PORT" == "8111" ]] || { echo "Váratlan Pandemic Service-port" >&2; exit 1; }

# Check direct app protection, not the currently protected public ingress.
"${KUBE[@]}" exec -n "$NS" deployment/pandemic -c pandemic -- python -c '
import json, os, urllib.error, urllib.request
assert os.environ.get("PANDEMIC_ACCESS_MODE") == "invite", "Invite mode is OFF"
assert os.environ.get("PANDEMIC_PUBLIC_ORIGIN") == "https://pmqxyz.hopto.org"
base = "http://127.0.0.1:8111"
with urllib.request.urlopen(base + "/pandemic/api/healthz", timeout=5) as r:
    health = json.load(r)
assert health.get("ok") is True and health.get("storage") == "redis", health
for path in ["/pandemic/", "/pandemic/static/app.js", "/pandemic/api/access/whoami"]:
    try:
        urllib.request.urlopen(base + path, timeout=5)
    except urllib.error.HTTPError as e:
        assert e.code == 401, (path, e.code)
    else:
        raise AssertionError("Unauthenticated URL accessible: " + path)
print("APP INVITE GATE PASS: protected paths returned 401")
'

"${KUBE[@]}" get ingress -A -o json | python3 -c '
import json, sys
for ing in json.load(sys.stdin).get("items", []):
    name = ing["metadata"]["name"]
    namespace = ing["metadata"]["namespace"]
    for r in ing.get("spec", {}).get("rules", []):
        if r.get("host") != "pmqxyz.hopto.org":
            continue
        for p in r.get("http", {}).get("paths", []):
            if p.get("path", "").rstrip("/") == "/pandemic" and (name, namespace) != ("pandemic-game-ingress","default"):
                raise SystemExit("Másik Ingress már kezeli /pandemic útvonalat: " + namespace + "/" + name)
'

"${KUBE[@]}" apply -f k8s/pandemic-ingress.yaml
"${KUBE[@]}" get ingress pandemic-game-ingress -n "$NS"
echo "A régi BasicAuth middleware-annotáció lekerült a Pandemic útvonalról."
echo "A middleware-erőforrás és a Secret változatlan: visszaállíthatók."
echo "Most teszteld a meghívólinkes belépést mindkét böngészőből!"
