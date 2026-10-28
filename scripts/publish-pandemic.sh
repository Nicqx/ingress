#!/usr/bin/env bash
# Publish Pandemic ONLY after app and BasicAuth secret are ready.
set -euo pipefail
cd "$(dirname "$0")/.."
KUBE=(sudo k3s kubectl)
NAMESPACE=default
if [[ "${1:-}" != "--apply" ]]; then
  echo "ELŐNÉZET: k8s/pandemic-ingress.yaml nincs alkalmazva."
  echo "Előfeltételek: pandemic-service Ready + bcrypt users Secret."
  echo "Aktiválás: bash scripts/publish-pandemic.sh --apply"
  exit 0
fi
"${KUBE[@]}" rollout status deployment/pandemic -n "$NAMESPACE" --timeout=90s
SERVICE_PORT=$("${KUBE[@]}" get service/pandemic-service -n "$NAMESPACE" -o jsonpath='{.spec.ports[0].port}')
[[ "$SERVICE_PORT" == "8111" ]] || { echo "Váratlan Pandemic service port." >&2; exit 1; }
"${KUBE[@]}" get secret pandemic-basic-auth -n "$NAMESPACE" >/dev/null
USERS=$("${KUBE[@]}" get secret pandemic-basic-auth -n "$NAMESPACE" -o jsonpath='{.data.users}')
[[ -n "$USERS" ]] || { echo "Üres BasicAuth users secret." >&2; exit 1; }
"${KUBE[@]}" get middleware -n "$NAMESPACE" >/dev/null
# Fail on any OTHER ingress that already owns /pandemic on this hostname.
"${KUBE[@]}" get ingress -A -o json | python3 -c '
import json, sys
x = json.load(sys.stdin)
for ing in x.get("items", []):
    name = ing["metadata"]["name"]
    namespace = ing["metadata"]["namespace"]
    for r in ing.get("spec", {}).get("rules", []):
        if r.get("host") != "pmqxyz.hopto.org":
            continue
        for path in r.get("http", {}).get("paths", []):
            if path.get("path", "").rstrip("/") == "/pandemic" and (name, namespace) != ("pandemic-game-ingress","default"):
                raise SystemExit("Másik Ingress már kezeli a /pandemic útvonalat: " + namespace + "/" + name)
'
"${KUBE[@]}" apply -f k8s/pandemic-ingress.yaml
"${KUBE[@]}" get middleware/pandemic-auth ingress/pandemic-game-ingress -n "$NAMESPACE"
echo "Most ellenőrizd, hogy jogosulatlan HTTPS-kérés 401-et kap, és a jogosult WebSocket működik."
