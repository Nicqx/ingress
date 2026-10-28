# Pandemic – védett HTTPS útvonal

**Állapot:** előkészített külön PR. Az éles útvonal egyelőre változatlan.

A Pandemic játék a belső pandemic-service:8111 Service mögött fut.
A k8s/pandemic-ingress.yaml két erőforrást hoz létre:
- Middleware/pandemic-auth: HTTP BasicAuth, bcrypt, removeHeader true
- Ingress/pandemic-game-ingress: pmqxyz.hopto.org/pandemic, HTTPS, my-tls-secret

A repo privát volta nem védi a publikus URL-t. A BasicAuth az
internet felől elérhető útvonalat jelszóval védi. Csak LAN-only
használathoz ne telepítsd az ingress-t, használj helyi port-forwardot.

## Előfeltételek

1. Pandemic Redis-alapú k3s Deployment Ready.
2. Kétböngészős helyi teszt és restart utáni visszacsatlakozás PASS.
3. A TLS tanúsítvány érvényes.
4. A BasicAuth Secret kizárólag a NUC-on, kézzel létrehozva.

## BasicAuth Secret – egyszeri létrehozás a NUC-on

Az apache2-utils csomag htpasswd parancsa interaktív jelszót kér.
A jelszót ne parancssori argumentumként add meg.

    sudo apt install apache2-utils
    umask 077
    AUTHFILE=$(mktemp)
    trap 'rm -f "$AUTHFILE"' EXIT
    htpasswd -nB pandemic > "$AUTHFILE"
    sudo k3s kubectl create secret generic pandemic-basic-auth --from-file=users="$AUTHFILE" -n default
    rm -f "$AUTHFILE"
    trap - EXIT

Ha a Secret már létezik, a create parancs szándékosan hibázik.

## Aktiválás

    cd ~/codes/ingress
    git fetch origin
    git switch --track origin/feature/pandemic-basic-auth-route
    bash scripts/publish-pandemic.sh
    bash scripts/publish-pandemic.sh --apply

A script ellenőrzi a Secretet, a Deploymentet, a 8111-es service-portot
és az ütköző útvonalakat. Nem módosít más ingress-erőforrásokat.

## Ellenőrzések

- Hitelesítés nélkül /pandemic/ HTTPS válasza 401.
- BasicAuth pandemic felhasználóval a böngésző megnyitja az oldalt.
- Mindkét böngészőben elérhető az élő WebSocket kapcsolat.
- A második játékos csatlakozása frissíti az első játékos lobbyját.
- A játékostokent az X-Pandemic-Token fejléc kezeli, nem az Authorization.

Amíg a WS kétszemélyes teszt nem PASS, a landing csempét ne aktiváld.

## Visszavonás

    sudo k3s kubectl delete ingress pandemic-game-ingress -n default
    sudo k3s kubectl delete middleware pandemic-auth -n default

Az auth Secret, a Service és a Redis-adatok megmaradnak.
Csak a Secret törlésével nem ajánlott visszavonni a route-ot.
