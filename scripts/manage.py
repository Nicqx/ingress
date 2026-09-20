#!/usr/bin/env python3
"""Traefik routes and explicit TLS migration/renewal; never replace a controller."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
from common import restore_snapshot
from pathlib import Path
import re
import urllib.request
from common import (ROOT, Failure, Kube, canonical, clean_resource, diagnose, entrypoint,
                    label, operation_lock, parser, private_write, run, snapshot)

MAIN = 'home-games-ingress'
SUDOKU = 'home-games-sudoku-ingress'
STRIP = 'nicqx-strip-sudoku-prefix'
ROUTES = {
    MAIN: [('/', 'landing-page-service', 80), ('/sumplete', 'sum-local-service', 8080),
           ('/tic-tac-toe', 'ultimate-tic-tac-toe-service', 8090), ('/chess', 'chess-game-service', 8099)],
    SUDOKU: [('/sudoku', 'sudoku-app', 9097)],
    'bakos-game-ingress': [('/bakos', 'bakos-game-service', 8105)],
    'maffia-game-ingress': [('/maffia', 'maffia-game-service', 8098)],
}
ALLOWED = {('Ingress', name) for name in ROUTES} | {('Middleware', STRIP)}
MIDDLEWARE_ANNOTATION = 'traefik.ingress.kubernetes.io/router.middlewares'
CERT_MANAGER_VERSION = 'v1.21.2'
CERT_MANAGER_SHA256 = 'e03b668ec8675214af6b0a671699d088f2601fa3878e0dbe1b41d3feafd1879f'


def config():
    local = ROOT / 'config.local.json'
    data = json.loads((local if local.exists() else ROOT / 'config.example.json').read_text())
    host = data.get('hostname', '')
    if not isinstance(host, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host) or '.' not in host:
        raise Failure('Ervenytelen hostname.')
    if data.get('tls_secret') != 'my-tls-secret':
        raise Failure('Ebben a migracioban csak a my-tls-secret kezelheto.')
    return data


def path_id(path):
    return path.rstrip('/') or '/'


def backend(path, service, port):
    return {'path': path, 'pathType': 'Prefix', 'backend': {'service': {'name': service, 'port': {'number': port}}}}


def discover_group(kube):
    crds = kube.get('crds', namespace=None)['items']
    for group in ['traefik.io', 'traefik.containo.us']:
        obj = next((c for c in crds if c['metadata']['name'] == 'middlewares.' + group), None)
        if obj and any(v['name'] == 'v1alpha1' and v.get('served') for v in obj['spec']['versions']):
            return group
    raise Failure('Hianyzik a Traefik Middleware CRD; a controllerhez nem nyulunk automatikusan.')


def render(settings, existing=None, group='traefik.io', middleware_objects=None):
    existing = existing or []
    middleware_objects = middleware_objects or {}
    host = settings['hostname']
    wanted = {path_id(p): name for name, routes in ROUTES.items() for p, _, _ in routes}
    # Abort duplicate route ownership; never delete an unfamiliar ingress to make ours win.
    for obj in existing:
        for rule in obj.get('spec', {}).get('rules', []):
            if rule.get('host') != host: continue
            for p in rule.get('http', {}).get('paths', []):
                route = path_id(p.get('path', '/'))
                if route in wanted and obj['metadata']['name'] != wanted[route]:
                    raise Failure(f"Utkozo ingress: {obj['metadata']['name']} ({route}); elobb egyeztetni kell a tulajdonosat.")
    result = []
    strip_needed = False
    for name, routes in ROUTES.items():
        old = next((o for o in existing if o['metadata']['name'] == name), None)
        if old:
            obj = clean_resource(old)
            spec = obj['spec']
            annotations = obj['metadata'].setdefault('annotations', {})
            ingress_class = spec.get('ingressClassName', annotations.get('kubernetes.io/ingress.class', 'traefik'))
            if ingress_class != 'traefik':
                raise Failure(f'{name}: nem Traefik ingress; nincs automatikus atiras.')
        else:
            obj = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress', 'metadata': {'name': name, 'annotations': {
                'traefik.ingress.kubernetes.io/router.entrypoints': 'websecure',
                'traefik.ingress.kubernetes.io/router.tls': 'true'}},
                'spec': {'ingressClassName': 'traefik', 'rules': []}}
            spec, annotations = obj['spec'], obj['metadata']['annotations']
        expected_paths = {path_id(p): (service, port) for p, service, port in routes}
        foreign = False
        rule = None
        for candidate in spec.setdefault('rules', []):
            if candidate.get('host') == host:
                if rule is not None:
                    raise Failure(f'{name}: tobbszoros host-szabaly; elobb kezi osszefesules szukseges.')
                rule = candidate
            for p in candidate.get('http', {}).get('paths', []):
                if candidate.get('host') != host or path_id(p.get('path', '/')) not in expected_paths:
                    foreign = True
        if rule is None:
            rule = {'host': host, 'http': {'paths': []}}
            spec['rules'].append(rule)
        paths = rule.setdefault('http', {}).setdefault('paths', [])
        for path, service, port in routes:
            matches = [p for p in paths if path_id(p.get('path', '/')) == path]
            if len(matches) > 1:
                raise Failure(f'{name}: duplikalt utvonal: {path}')
            if matches:
                current = matches[0]
                if current.get('backend', {}).get('service', {}).get('name') != service:
                    raise Failure(f'{name}: {path} masik szolgaltatast hasznal; nincs feluliras.')
                current.update(backend(path, service, port))
            else:
                paths.append(backend(path, service, port))
        tls = spec.setdefault('tls', [])
        tls_matches = [t for t in tls if host in t.get('hosts', [])]
        if tls_matches and any(t.get('secretName') != settings['tls_secret'] for t in tls_matches):
            raise Failure(f'{name}: eltero TLS secret; nincs automatikus feluliras.')
        if not tls_matches:
            if foreign: raise Failure(f'{name}: mas alkalmazas is hasznalja; TLS modositas elott felmeres szukseges.')
            tls.append({'hosts': [host], 'secretName': settings['tls_secret']})
        refs = [x.strip() for x in annotations.get(MIDDLEWARE_ANNOTATION, '').split(',') if x.strip()]
        strips = []
        for ref in refs:
            middleware = middleware_objects.get(ref, {})
            if 'stripPrefix' in middleware.get('spec', {}) or 'stripPrefixRegex' in middleware.get('spec', {}):
                strips.append(middleware['spec'])
        if name == SUDOKU:
            good_strip = any(x.get('stripPrefix', {}).get('prefixes') == ['/sudoku'] for x in strips)
            if strips and not good_strip:
                raise Failure('A Sudoku middleware mas prefixet vag le; nincs automatikus csere.')
            own_ref = 'default-' + STRIP + '@kubernetescrd'
            if own_ref in refs or not good_strip:
                if foreign: raise Failure('A Sudoku ingress mas alkalmazast is tartalmaz; nincs globalis middleware-valtoztatas.')
                if own_ref not in refs: refs.append(own_ref)
                strip_needed = True
                annotations[MIDDLEWARE_ANNOTATION] = ','.join(refs)
        elif strips:
            raise Failure(f'{name}: a backend varja a prefixet, de middleware levagja. Elobb a meglevo szabalyokat kell tisztazni.')
        result.append(obj)
    if strip_needed:
        result.insert(0, {'apiVersion': group + '/v1alpha1', 'kind': 'Middleware',
                          'metadata': {'name': STRIP}, 'spec': {'stripPrefix': {'prefixes': ['/sudoku']}}})
    return label(result, 'ingress')


def check_tls(secret, host):
    try:
        if secret['type'] != 'kubernetes.io/tls': raise ValueError()
        cert = base64.b64decode(secret['data']['tls.crt'], validate=True)
        key = base64.b64decode(secret['data']['tls.key'], validate=True)
        run(['openssl', 'x509', '-noout', '-checkend', '172800'], cert, sensitive=True)
        # OpenSSL x509 may return exit status 0 even for a hostname mismatch.
        matched = run(['openssl', 'x509', '-noout', '-checkhost', host], cert, sensitive=True)
        if b'does match certificate' not in matched: raise ValueError()
        cert_public = run(['openssl', 'x509', '-pubkey', '-noout'], cert, sensitive=True).strip()
        key_public = run(['openssl', 'pkey', '-pubout'], key, sensitive=True).strip()
        if cert_public != key_public: raise ValueError()
    except (KeyError, TypeError, ValueError, Failure):
        raise Failure('A TLS secret hianyzik, nem ehhez a hosthoz tartozik, hibas kulcsu vagy 48 oran belul lejar.') from None


def export_tls(kube, settings, file):
    secret = kube.get('secret', settings['tls_secret'])
    check_tls(secret, settings['hostname'])
    portable = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': settings['tls_secret'], 'namespace': 'default'},
                'type': 'kubernetes.io/tls', 'data': {k: secret['data'][k] for k in ['tls.crt', 'tls.key']}}
    raw = canonical({'format': 'nicqx-tls-v1', 'source_cluster_uid': kube.identity, 'secret': portable})
    dest = private_write(file, raw)
    private_write(str(dest) + '.sha256', (hashlib.sha256(raw).hexdigest() + '\n').encode())
    print(f'TLS export (privat kulccsal, 0600): {dest}')


def import_tls(kube, settings, file, dry_run):
    raw = Path(file).read_bytes()
    if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != Path(str(file) + '.sha256').read_text().strip():
        raise Failure('Hibas TLS export meret/ellenorzoosszeg.')
    try:
        data = json.loads(raw)
        if data['format'] != 'nicqx-tls-v1' or data['source_cluster_uid'] == kube.identity: raise ValueError()
        secret = data['secret']
        check_tls(secret, settings['hostname'])
        # Discard all source ownership/annotations. Only two expected data keys travel.
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': settings['tls_secret'], 'namespace': 'default'},
                  'type': 'kubernetes.io/tls', 'data': {k: secret['data'][k] for k in ['tls.crt', 'tls.key']}}
    except (KeyError, TypeError, ValueError):
        raise Failure('Ervenytelen TLS export.') from None
    old = kube.get('secret', settings['tls_secret'])
    if old:
        if old.get('data') != secret['data']:
            raise Failure('A celon mar masik TLS secret van. Nem irjuk felul automatikusan.')
        print('Azonos TLS secret mar a celon van.'); return
    if not dry_run: snapshot(kube, [secret])
    kube.apply([secret], {('Secret', settings['tls_secret'])}, dry_run=dry_run, sensitive=True)


def install_cert_manager(kube):
    if kube.target != 'nuc':
        raise Failure('A Pi regi cert-manager telepiteset es K3s verziojat nem frissitjuk ezzel.')
    nodes = kube.get('nodes', namespace=None)['items']
    version = nodes[0]['status']['nodeInfo']['kubeletVersion']
    match = re.match(r'v1\.(\d+)\.', version)
    if not match or not 33 <= int(match[1]) <= 36:
        raise Failure('Ez a cert-manager kiadas Kubernetes 1.33-1.36 verziohoz van ellenorizve.')
    if kube.get('namespace', 'cert-manager', namespace=None) or any(
        c['metadata']['name'].endswith('cert-manager.io') for c in kube.get('crds', namespace=None)['items']):
        raise Failure('Mar letezik cert-manager telepites/CRD. Ez a parancs csak ures NUC-ra telepit.')
    url = f'https://github.com/cert-manager/cert-manager/releases/download/{CERT_MANAGER_VERSION}/cert-manager.yaml'
    with urllib.request.urlopen(url, timeout=60) as response:
        manifest = response.read(4 * 1024 * 1024)
    if hashlib.sha256(manifest).hexdigest() != CERT_MANAGER_SHA256:
        raise Failure('A cert-manager kiadas ellenorzoosszege elter; nincs telepites.')
    # Namespace/CRD bootstrap cannot be one server dry-run (dependent API objects do not yet exist).
    kube.call('apply', '-f', '-', data=manifest, timeout=210)
    for name in ['certificates.cert-manager.io', 'clusterissuers.cert-manager.io']:
        kube.call('wait', '--for=condition=Established', 'crd/' + name, '--timeout=120s', timeout=150)
    for name in ['cert-manager', 'cert-manager-cainjector', 'cert-manager-webhook']:
        kube.call('rollout', 'status', 'deployment/' + name, '-n', 'cert-manager', '--timeout=180s', timeout=210)
    print('cert-manager telepitve. A megujitas kulon enable-renewal parancs, a port80 atiranyitasa utan.')


def enable_renewal(kube, settings, dry_run):
    crds = {c['metadata']['name'] for c in kube.get('crds', namespace=None)['items']}
    if not {'certificates.cert-manager.io', 'clusterissuers.cert-manager.io'} <= crds:
        raise Failure('Hianyzik a cert-manager. Elobb install-cert-manager szukseges a NUC-on.')
    email = settings.get('acme_email', '')
    if not isinstance(email, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise Failure('Ervenyes acme_email szukseges a config.local.json fajlban.')
    issuer = kube.get('clusterissuer', 'letsencrypt-prod', namespace=None)
    if issuer:
        issuer = clean_resource(issuer)
        if issuer.get('spec', {}).get('acme', {}).get('server') != 'https://acme-v02.api.letsencrypt.org/directory':
            raise Failure('A letezo issuer eltero ACME szervert hasznal; nem modositjuk.')
    else:
        issuer = {'apiVersion': 'cert-manager.io/v1', 'kind': 'ClusterIssuer', 'metadata': {'name': 'letsencrypt-prod'},
                  'spec': {'acme': {'server': 'https://acme-v02.api.letsencrypt.org/directory', 'email': email,
                    'privateKeySecretRef': {'name': 'letsencrypt-prod'}, 'solvers': [{'http01': {'ingress': {'ingressClassName': 'traefik'}}}]}}}
    cert = kube.get('certificate', settings['tls_secret'])
    if cert:
        spec = cert['spec']
        if (spec.get('secretName') != settings['tls_secret'] or spec.get('dnsNames') != [settings['hostname']]
                or spec.get('issuerRef', {}).get('name') != 'letsencrypt-prod'
                or spec.get('issuerRef', {}).get('kind') != 'ClusterIssuer'):
            raise Failure('A meglevo Certificate eltero beallitast hasznal; nincs automatikus csere.')
        cert = clean_resource(cert)
    else:
        cert = {'apiVersion': 'cert-manager.io/v1', 'kind': 'Certificate',
                'metadata': {'name': settings['tls_secret'], 'namespace': 'default'},
                'spec': {'secretName': settings['tls_secret'], 'dnsNames': [settings['hostname']],
                         'issuerRef': {'name': 'letsencrypt-prod', 'kind': 'ClusterIssuer'},
                         'privateKey': {'rotationPolicy': 'Always'}}}
    # Existing issuer/certificate specs are preserved, including other solver configuration.
    kube.apply([issuer, cert], {('ClusterIssuer', 'letsencrypt-prod'), ('Certificate', settings['tls_secret'])}, dry_run=dry_run)
    if not dry_run:
        kube.call('wait', '--for=condition=Ready', 'certificate/' + settings['tls_secret'], '-n', 'default', '--timeout=180s', timeout=210)


def main():
    p = parser('Traefik utvonalak es TLS koltoztetes',
               ['update', 'rollback', 'render', 'diagnose', 'export-tls', 'import-tls', 'install-cert-manager', 'enable-renewal'])
    p.add_argument('--file')
    args = p.parse_args()
    settings = config()
    if args.command == 'render': print(json.dumps(render(settings), indent=2)); return
    if args.command in {'export-tls', 'import-tls'} and not args.file: p.error('--file szukseges')
    if args.dry_run and args.command not in {'update', 'rollback', 'import-tls', 'enable-renewal'}: p.error('Ehhez a parancshoz nincs dry-run')
    kube = Kube(args.target, args.context)
    kube.verify(require_ready=args.command != 'diagnose')
    if args.command == 'diagnose': diagnose(kube); return
    with operation_lock(args.target):
        if args.command == 'rollback':
            if not args.file: p.error('--file szukseges')
            restore_snapshot(kube, args.file, ALLOWED, args.dry_run)
            return
        if args.command == 'update':
            check_tls(kube.get('secret', settings['tls_secret']), settings['hostname'])
            group = discover_group(kube)
            middlewares = {'default-' + m['metadata']['name'] + '@kubernetescrd': m
                           for m in kube.get('middlewares.' + group)['items']}
            items = render(settings, kube.get('ingresses')['items'], group, middlewares)
            if not args.dry_run: snapshot(kube, items)
            kube.apply(items, ALLOWED, dry_run=args.dry_run)
        elif args.command == 'export-tls': export_tls(kube, settings, args.file)
        elif args.command == 'import-tls': import_tls(kube, settings, args.file, args.dry_run)
        elif args.command == 'install-cert-manager': install_cert_manager(kube)
        elif args.command == 'enable-renewal': enable_renewal(kube, settings, args.dry_run)


if __name__ == '__main__':
    entrypoint(main)
