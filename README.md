# Traefik ingress – Pi5 és NUC

Egyetlen kezelt útvonal tartozik minden játékhoz, a Pi jelenlegi erőforrásneveivel. Az update a meglévő ingressbe csak az ismert játékútvonalakat illeszti be; egyéb hostokat, útvonalakat és annotációkat megőriz. Ha egy ismert útvonal másik szolgáltatásé, duplikált ingress ütközik vele vagy közös szabály globális átírása kellene, módosítás nélkül megáll. Régi ingress-nginx/Traefik controllert nem töröl és nem konfigurál át.

| Ingress | Útvonal | Service / port |
|---|---|---|
| `home-games-ingress` | `/` | `landing-page-service:80` |
| `home-games-ingress` | `/sumplete` | `sum-local-service:8080` |
| `home-games-ingress` | `/tic-tac-toe` | `ultimate-tic-tac-toe-service:8090` |
| `home-games-ingress` | `/chess` | `chess-game-service:8099` |
| `home-games-sudoku-ingress` | `/sudoku` | `sudoku-app:9097` |
| `bakos-game-ingress` | `/bakos` | `bakos-game-service:8105` |
| `maffia-game-ingress` | `/maffia` | `maffia-game-service:8098` |

Minden útvonal `Prefix`. Csak a Sudoku igényel prefixlevágást. A többi alkalmazás maga kezeli a saját prefixét, így a WebSocket/Socket.IO útvonalak is megmaradnak.

A Middleware API-t a clusterből fedezzük fel: elsődlegesen `traefik.io/v1alpha1`, régi Pi-telepítéshez a ténylegesen kiszolgált `traefik.containo.us/v1alpha1` is kezelhető. Új NUC-ingressek csak a `websecure` entrypointra kerülnek TLS-szel. A meglévő Pi-ingressek globális entrypoint/middleware beállítását megőrizzük; **általános HTTP→HTTPS redirectet ez az update nem kapcsol be**. A 80-as port a cert-manager HTTP-01 ellenőrzéséhez szükséges.

## Közös használat

A parancsokat a repo könyvtárából futtasd. Előfeltétel: Bash, Git, Python 3.9+ és a helyi clusterhez hozzáférő `kubectl` (az ingresshez OpenSSL is kell). Python-csomag telepítése nem szükséges.

```bash
git pull --ff-only
./update.sh --target pi5 --dry-run
./update.sh --target pi5
```

A NUC-on `--target nuc` kell. A parancs ellenőrzi a node nevét és architektúráját; többnode-os clusterhez szándékosan nincs automatikus telepítés. Más kube-context: `--context NEV`. Ha a kubeconfig csak sudo-val olvasható, a teljes script helyett csak a kubectl kapjon jogosultságot:

```bash
KUBECTL='sudo k3s kubectl' ./update.sh --target nuc
```

Az update tiszta munkakönyvtárban `git pull --ff-only` után dolgozik. A `--dry-run` a jelenlegi helyi kódot ellenőrzi a Kubernetes API-val, nem pullol és nem ír a clusterbe. A tudatosan helyi kódhoz `--no-pull` használható. Nincs `reset --hard`, force push, prune vagy tömeges erőforrás-törlés.

Diagnosztika és kapcsolat nélküli manifest-előnézet:

```bash
./scripts/diagnose.sh --target pi5
python3 scripts/manage.py render --target nuc
```

A módosítás előtt a korábbi manifestek `0600` jogosultságú mentése készül a `~/.local/state/nicqx-infra/<target>/<repo>/` könyvtárba. A parancs kiírja a pontos fájlnevet. Más mentési gyökér: `NICQX_STATE_DIR`. A négy repo ugyanazon felhasználó/gép/cél műveleteit helyi zárral sorosítja; több operátor között külön egyeztetés szükséges.

Manifest-visszaállítás a **kiírt mentési fájl** teljes elérési útjával:

```bash
python3 scripts/manage.py rollback --target pi5 --file /teljes/ut/manifest-mentes.json --dry-run
python3 scripts/manage.py rollback --target pi5 --file /teljes/ut/manifest-mentes.json
```

Ez csak ugyanabban a clusterben, a repo engedélyezett erőforrásaira működik. Nem állít vissza adatbázist, és nem törli az időközben létrehozott erőforrásokat. Sikertelen rolloutnál az update hibával áll le; a visszaállítás külön, látható művelet.

Az `availability-calendar`, `availability-calendar-service`, `connectivity-check`, `munkaido`, `munkaido-nyilvantarto`, `rsvp1984`, `rsvp1985` neveket a közös eszköz védi. Más, ismeretlen erőforrásokat sem alkalmaz a repo saját engedélylistáján kívül. Dockerhez, K3s szolgáltatáshoz vagy routerhez egyik update sem nyúl.

## TLS előkészítése

Az alap hostname `pmqxyz.hopto.org`, a kezelt Secret `default/my-tls-secret`. Helyi eltéréshez `config.local.json` készíthető a példából. Az ingress update előtt a tanúsítványnak léteznie kell, illeszkednie kell a hostname-hez és a privát kulcshoz, továbbá legalább 48 óráig érvényesnek kell lennie.

Pi:

```bash
python3 scripts/manage.py export-tls --target pi5 --file "$HOME/tls-export.json"
```

Az exportot és a `.sha256` fájlt SCP/SSH kapcsolaton másold át. A JSON privát kulcsot tartalmaz; ne commitold és ne másold chatbe. NUC:

```bash
python3 scripts/manage.py import-tls --target nuc --file "$HOME/tls-export.json" --dry-run
python3 scripts/manage.py import-tls --target nuc --file "$HOME/tls-export.json"
./update.sh --target nuc
```

A forráscluster owner reference/UID/annotációi nem kerülnek át. Más meglévő céloldali tanúsítványt a script nem ír felül; azonos tanúsítvánnyal az import ismételhető. A fájl SHA-256 ellenőrzőösszege szállítási hibát jelez, nem helyettesíti a megbízható forrást és az SSH-t.

## Cert-manager a NUC-on

A megadott NUC-on nincs cert-manager. Kezdeti telepítés, a forrásból ellenőrzött SHA-256-tal rögzített **v1.21.2** manifestből:

```bash
python3 scripts/manage.py install-cert-manager --target nuc
```

Ez külön, explicit infrastruktúra-telepítés: új `cert-manager` namespace, CRD-k, szükséges RBAC és a három vezérlő. Kubernetes 1.33–1.36 és üres cert-manager telepítés esetén engedélyezett. Meglévő telepítést és a Pi régi 1.12-es verzióját nem frissíti. Részleges telepítési hibánál a kimenetet és a cert-manager podokat vizsgáld meg; ne töröld találomra a CRD-ket. A letöltéshez GitHub-, az image-ekhez registry-elérés kell.

A tanúsítvány megújítását **a router 80-as továbbításának ellenőrzése után** engedélyezd:

```bash
python3 scripts/manage.py enable-renewal --target nuc
kubectl get certificate,clusterissuer -A
```

Új `letsencrypt-prod` ClusterIssuer és explicit Certificate csak ezzel a paranccsal keletkezik. Meglévő kompatibilis issuer/certificate beállításait megőrizzük. Az ACME-kapcsolati emailt ellenőrizd a konfigurációban. Az új ingressek nem tartalmaznak automatikus tanúsítványigénylést indító annotációt, így előkészítés közben nem indítanak ismétlődő challenge-eket a még Pi-re mutató routeren.

A `Certificate Ready=True` egy listában nem helyettesíti az aktuális cert lejáratának ellenőrzését, különösen leállt controller mellett.

## Költözés és ellenőrzés

A részletes, a jelenlegi leltárra szabott sorrend a [MIGRATION.md](MIGRATION.md) fájlban van. A régi, egymással átfedő YAML-ok szándékosan kikerültek; ne futtass `kubectl apply -f .` parancsot ebből a repóból.

Teszt: `python3 -m unittest discover -s tests -v`. A tesztek ellenőrzik az útvonaltulajdonlást, a védett route megőrzését, a régi/új CRD-t és valódi OpenSSL-lel a tanúsítvány hostname/kulcs-ellenőrzését.

Hivatalos források: [K3s hálózati szolgáltatások](https://docs.k3s.io/networking/networking-services), [cert-manager támogatott kiadások](https://cert-manager.io/docs/releases/), [cert-manager telepítés](https://cert-manager.io/docs/installation/kubectl/).
