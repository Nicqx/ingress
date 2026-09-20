# Pi5 → NUC: ellenőrzött lépések

Ez a kör a `redis`, `noip`, `landing`, `ingress` repókat egységesíti. A játékok kódját/image-eit és a működő, külön kezelt alkalmazásokat nem cseréli le. **A négy repo frissítése önmagában még nem teszi készre a teljes NUC-os átállást.**

## 1. Kiinduló állapot és az első lépés

| | Pi5 | NUC |
|---|---|---|
| Node / architektúra | `pi5`, ARM64 | `nuc`, AMD64 |
| Node LAN IP | `192.168.1.6` (a hoston `.7` is szerepel) | `192.168.1.10` |
| K3s | 1.31.5 | 1.35.4 |
| Traefik | 2.11.18, LB `.6` | 3.6.13, LB `pending` |
| Traefik NodePort HTTP / HTTPS | 30949 / 32749 | 31942 / 31930 |
| Redis | master + replica nem Ready | master + replica Ready, meglévő PVC |
| cert-manager | 1.12, a vezérlők nem Ready | nincs telepítve |
| No-IP | a leltárban nincs | már aktív |

Ezek a kapott pillanatfelvétel adatai, nem helyszíni mérések. A Pi5-ön a legtöbb alkalmazás `0/1`; a Deployment-táblából nem derül ki, hogy image-letöltés, tárhely, ütemezés vagy más hiba okozza. **Előbb ezt kell tisztázni.** Hibás Redis mellett a mentés/frissítés megáll, és nem törli a PVC-t.

A friss repóban elsőként, mindkét gépen a megfelelő targettel:

```bash
git pull --ff-only
./scripts/diagnose.sh --target pi5
kubectl describe pod -n default redis-master-0
```

Ha szükséges, a Redis aktuális/előző konténernaplója külön ellenőrizhető:

```bash
kubectl logs -n default redis-master-0 -c redis --tail=100
kubectl logs -n default redis-master-0 -c redis --previous --tail=100
```

Az előző napló hiánya önmagában nem hiba. Ne törölj PVC-t, és ne futtass `kubectl delete ... --all` vagy teljes K3s-leállítást.

## 2. Védett alkalmazások és a munkaidő-kezelő

Az `availability-calendar` / `availability-calendar-service`, `connectivity-check`, valamint a Pi két RSVP-erőforrása kimarad. A NUC-on már meglévő Redis adatait szintén megőrizzük, mert nem feltételezzük, hogy csak a játékok használják.

A `Nicqx/munkaido-nyilvantarto` repo forrása alapján ez **Docker Compose**, nem Kubernetes: a konténer neve `munkaido-nyilvantarto`, a host portja **1985**, az SQLite fájl a repo `data/munkaido.db` útvonalán van (`./data:/app/data` kötet). Ez magyarázza, miért nem szerepel a `kubectl` listában; hogy melyik gépen fut ténylegesen, azt helyben kell megállapítani:

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Ez az alkalmazás, a `data` könyvtára és a saját környezeti beállításai most érintetlenek. Docker-szolgáltatást vagy minden konténert leállító parancs nem része az átállásnak.

## 3. A négy repo frissítése

A Pi Redis-hibájának megoldása és sikeres mentés után, az egyes repo könyvtárakban:

```bash
git pull --ff-only
./update.sh --target pi5 --dry-run
./update.sh --target pi5
```

Sorrend: **redis → landing → noip → ingress**. A No-IP a Pi-n új telepítésként nullán marad. Meglévő Redisnél az update megtartja az élő image-et és beállításokat, és csak sikeres RDB-mentéssel folytatja. A közös Redis új verzióra/ACL-re állítása külön, a kliensek felmérése utáni feladat.

A NUC-on `--target nuc` a cél. Ott is előbb Redis-mentés, utána landing és a meglévő No-IP frissítése következik. Az ingresshez előbb az alábbi TLS-import kell. Az update nem indítja el a nullára állított meglévő Deploymenteket.

## 4. Tanúsítvány és NUC ingress előkészítése

A Pi-n, az ingress repo könyvtárában:

```bash
mkdir -m 700 -p "$HOME/nicqx-migration"
python3 scripts/manage.py export-tls --target pi5 --file "$HOME/nicqx-migration/tls-export.json"
scp "$HOME/nicqx-migration/tls-export.json" "$HOME/nicqx-migration/tls-export.json.sha256" nicqx@192.168.1.10:~/
```

Az export privát kulcsot tartalmaz; a fájl és a célpéldány jogosultsága maradjon `0600`. A script nem ír felül régi exportot: ismétléskor új fájlnevet válassz. Ha a tanúsítvány lejárt vagy 48 órán belül lejár, előbb a Pi meglévő megújítási hibáját kell rendezni; lejárt cert nem kerül automatikusan át.

A NUC-on az ingress repo könyvtárában:

```bash
python3 scripts/manage.py import-tls --target nuc --file "$HOME/tls-export.json" --dry-run
python3 scripts/manage.py import-tls --target nuc --file "$HOME/tls-export.json"
./update.sh --target nuc --dry-run
./update.sh --target nuc
python3 scripts/manage.py install-cert-manager --target nuc
```

A cert-manager telepítés csak az új NUC-on, meglévő cert-manager nélküli környezetben működik. A Pi cert-manager/K3s verzióját nem frissíti. A másolt certtel a NUC már kiszolgálhat HTTPS-t; a megújítás bekapcsolása később, a 80-as port helyes irányítása után történik.

## 5. NUC elérés ellenőrzése a router átállítása nélkül

A `LoadBalancer pending` önmagában nem bizonyítja, hogy a Traefik hibás. Lehet ServiceLB-beállítás, portütközés vagy ütemezési probléma; ezt a `diagnose`, a kube-system podok és események döntik el. **Ne írjuk át vakon a Traefik/ServiceLB beállításait**, mert más szolgáltatás is használhat hostportot.

A jelenlegi NUC HTTPS NodePortja **31930**. Egy LAN-os gépről ez a parancs a publikus hostname-et és SNI-t megtartva közvetlenül a NUC-ot éri el:

```bash
curl --connect-to pmqxyz.hopto.org:443:192.168.1.10:31930 https://pmqxyz.hopto.org/
```

Ne használd a `-k` kapcsolót az elfogadási ellenőrzéshez: a certnek megbízhatónak és a hostname-hez illőnek kell lennie. Ha a NodePortot helyi tűzfal blokkolja, azt a hálózati ellenőrzés mutatja meg; nem nyitunk automatikusan új portokat.

A játékok későbbi frissítése után ugyanígy próbáld végig a hat játék útvonalát. Böngészős ellenőrzéshez ideiglenes helyi hosts/DNS-bejegyzés és megfelelő 443-as elérés, vagy külön tesztkörnyezet szükséges; a gép IP-jének közvetlen megnyitása nem ellenőrzi helyesen a hostname-alapú routingot és TLS-t.

## 6. A játékok következő köre és az adatköltöztetés

A NUC-on a megadott állapotban a sakk, Sumplete és amőba nem Ready; Bakos/Maffia telepítés nincs a listában. Az alkalmazásrepók következő körében kell egységesíteni a buildet, javítani a readiness/probe és alkalmazásbiztonsági hibákat, és **AMD64 image-eket** előállítani. A Pi ARM64 image-eit ne másold át futtatási megoldásként az AMD64 NUC-ra.

Az adatköltözést csak akkor végezd el, amikor a NUC-on minden átkerülő játék telepíthető és a route-ok kipróbálhatók. A sessionök többsége rövid életű; a napokkal előre készített export lejárhat.

A NUC-on, a redis repo könyvtárában előbb ments és állítsd meg az átkerülő játékok írását:

```bash
python3 scripts/manage.py backup --target nuc --file "$HOME/nuc-before-migration.rdb"
python3 scripts/manage.py pause-games --target nuc --file "$HOME/nuc-pause.json"
```

A Pi-n, a redis repo könyvtárában:

```bash
python3 scripts/manage.py pause-games --target pi5 --file "$HOME/pi5-pause.json"
python3 scripts/manage.py backup --target pi5 --file "$HOME/pi5-before-migration.rdb"
python3 scripts/manage.py export-games --target pi5 --file "$HOME/games-export.json"
scp "$HOME/games-export.json" "$HOME/games-export.json.sha256" nicqx@192.168.1.10:~/
```

Ha valamelyik játék később költözik, **mindkét pause és a forrás export** azonos, szűkebb `--apps` listával fusson, például `--apps chess ttt`. Ne futtasd újra a pause-t az eredeti állapotfájl lecserélésére; az első fájl tartalmazza a működő replikaszámokat.

A NUC-on:

```bash
python3 scripts/manage.py import-games --target nuc --file "$HOME/games-export.json"
python3 scripts/manage.py resume-games --target nuc --file "$HOME/nuc-pause.json"
kubectl get deployment,statefulset,pods -n default
```

Az import maga is teljes céloldali RDB-mentéssel kezdődik. Ütközésnél leáll, más alkalmazás kulcsát nem írja felül, és megőrzi az eredeti lejáratokat. Az exportált formátumot/szállítási ellenőrzőösszeget is ellenőrzi. Régi közös `session:*` kulcsok vagy egyedi Redis-prefix esetén külön felmérés kell.

A resume a mentett replikaszámot állítja vissza. A már korábban nullán álló játék ettől még nem indul el. Várd meg a tényleges Ready állapotot és próbálj ki egy új szobát, csatlakozást, lépést, frissítést és – ahol van – WebSocket-kapcsolatot. A védett alkalmazások működését is ellenőrizd.

## 7. Router és megújítás: csak a végső átváltáskor

A domain ugyanarra a publikus IP-re mutathat tovább; a DDNS maradhat a NUC-on. A belső címeket az alkalmazáskód nem tartalmazza.

Ha a NUC 80/443 elérése ServiceLB-n át később helyreáll, a router szokásos célja `192.168.1.10:80` és `:443` lehet. **A mostani, tesztelendő NodePort-alternatíva** külön Traefik-átalakítás nélkül:

| Router WAN TCP port | NUC cél |
|---|---|
| 80 | `192.168.1.10:31942` |
| 443 | `192.168.1.10:31930` |

Átállítás előtt olvasd vissza a tényleges NodePortokat `kubectl get service traefik -n kube-system` paranccsal. A routernek tudnia kell eltérő külső/belső portot megadni. Ha az adott router ezt nem tudja, előbb a NUC 80/443 elérését kell rendezni. Más meglévő porttovábbításokat – így esetleges 1985-ös munkaidő-kezelőt – ne változtass meg.

Egy hostname/80/443 átirányítása az összes rajta lévő route-ot érinti. Minden ott publikált szolgáltatás tulajdonosát ellenőrizni kell; még a Pi-n maradó játék vagy idegen útvonal mellett nincs teljes átváltás. A hiányzó szolgáltatásokra most nem készítünk automatikus Pi felé proxyzást.

A 80/443 átállítása után, mobilinternetről is próbáld a HTTPS-t. Utána a NUC ingress repóban:

```bash
python3 scripts/manage.py enable-renewal --target nuc
kubectl get certificate,clusterissuer -A
```

Az HTTP-01 megújításhoz a publikus DNS és a WAN 80 a NUC Traefikjére kell mutasson. IPv6/AAAA rekord esetén annak útvonalát is ellenőrizd. A tanúsítványt megújító két clustert ne hagyd hosszabb távon versenyezni ugyanazért a hostname-ért.

## 8. Visszalépés és későbbi eltávolítás

Ha még nem keletkezett új játékadat a NUC-on: állítsd meg a NUC játékait a mentett állapot megtartásával, irányítsd vissza a routert a korábbi célra, majd a Pi redis repóban:

```bash
python3 scripts/manage.py resume-games --target pi5 --file "$HOME/pi5-pause.json"
```

Ha már fogadtál új játékadatot a NUC-on, a Pi korábbi adata elavult lehet. Ilyenkor mindkét oldal játékírását meg kell állítani és egyeztetett visszamigráció szükséges; a router egyszerű visszaállítása önmagában adatvesztést okozhat. A script ilyenkor sem ír felül ütköző adatot.

A Pi játékai és a régi konfigurációk maradjanak meg leállított állapotban az ellenőrzési időszakra. A Redis PVC, cert Secret és teljes K3s eltávolítása nem része ennek a változásnak. Törlés csak sikeres NUC-üzem, kipróbált mentés és a Pi-n maradó kliensek leltára után következzen. A `local-path` tároló reclaim policy-ja `Delete`, ezért a PVC törlése adatvesztést jelenthet.

## Mit ellenőriznek az automatikus tesztek?

Hibás gépcél/namespace és tiltott szolgáltatás módosításának blokkolása; szerveroldali dry-run; privát mentési fájlok; Redis-konfiguráció megőrzése; valódi Redis adat/TTL/ütközés/hibás dump tesztek; No-IP hibakódok és hitelesítőadat-kezelés; nginx futtatási beállítások; ingress útvonalak és valódi OpenSSL certellenőrzés. A GitHub CI nem ér el a Pi/NUC clusterhez, és nem helyettesíti a helyi dry-runt, rolloutot és külső elérési próbát.
