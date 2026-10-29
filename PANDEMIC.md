# Pandemic: egyszeri meghívólink, nincs felhasználónév/jelszó

Ez az ág a meglévő pandemic-game-ingress útvonalról a **régi BasicAuth
middleware hivatkozását** leveszi, de **csak akkor**, ha a Pandemic backend
már meghívólinkes, Redisben ellenőrzött munkamenet-kaput használ.
A régi BasicAuth Kubernetes Secret és Middleware megmarad, hogy szükség
esetén gyorsan visszaállítható legyen.

## Telepítési sorrend – kötelező

1. A Pandemic repo feature/invite-link-access ágán 78/78 teszt PASS.
2. A Pandemic `bash scripts/deploy-nuc.sh --apply` telepítése sikeres.
3. Két személyes meghívólink kiadása a podból (lásd a Pandemic repo
   docs/PASSWORDLESS_INVITES.md leírását).
4. Ingress repo tesztek sikeresek.
5. `bash scripts/publish-pandemic.sh --apply` futtatása.
6. Két böngészős belépési és WebSocket-próba sikeres.
7. Csak ezután aktiválható a Pandemic csempe a landing repóban.

## Külön előnézet és élesítés

    cd ~/codes/ingress
    git fetch origin
    git switch --track origin/feature/pandemic-invite-switch
    python3 -m unittest discover -s tests -v
    bash scripts/publish-pandemic.sh
    bash scripts/publish-pandemic.sh --apply

A script közvetlenül a Pandemic podban vizsgálja meg:

- Az invite mód tényleg be van kapcsolva.
- A publikus HTTPS-origin megfelelő.
- A Redis healthz válasza OK.
- A védelem nélküli /pandemic/, /pandemic/static/app.js és
  /pandemic/api/access/whoami kérések **401** választ kapnak.

Ha bármelyik ellenőrzés sikertelen, a már működő Traefik BasicAuth
változatlan marad. A script más Ingress objektumát nem módosítja.

## Próba

- A névtelen /pandemic/ válasza 401 (már a Pandemic backendtől).
- A személyes meghívó URL felhasználónév/jelszó nélkül nyit.
- Az egyszer használatos link Belépés gombja 90 napos cookie-t ad.
- Újratöltéskor nincs új bejelentkezés.
- Két külön böngészőben működik az élő WebSocket és a játék.

## Visszaállítás az előző BasicAuth-ra

    sudo k3s kubectl annotate ingress/pandemic-game-ingress -n default 'traefik.ingress.kubernetes.io/router.middlewares=default-pandemic-auth@kubernetescrd' --overwrite

Ez nem módosítja a játékállapotot, a redis-t vagy más alkalmazást.
A régi Secretet és Middleware-t ne töröld a kétböngészős próba előtt.

## Biztonsági megjegyzés

A cookie/token a belépési hitelesítés; annak ellopása belépést tesz
lehetővé az adott eszközön. Ezért csak saját vagy megbízható eszközről
használjátok, és elveszett eszköz esetén a Pandemic
`python -m pandemic.access revoke --label ...` parancsával
vond vissza a személyes munkameneteket.
