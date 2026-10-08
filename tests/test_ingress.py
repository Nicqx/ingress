import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import manage
from common import Failure


CONFIG = {'hostname': 'pmqxyz.hopto.org', 'tls_secret': 'my-tls-secret'}


class RouteTests(unittest.TestCase):
    def initial(self): return [o for o in manage.render(CONFIG) if o['kind'] == 'Ingress']

    def test_one_owner_per_host_path_and_only_sudoku_strips(self):
        items = manage.render(CONFIG)
        routes = [(r['host'], p['path']) for i in items if i['kind'] == 'Ingress'
                  and i['metadata']['name'] in manage.ROUTES
                  for r in i['spec']['rules'] for p in r['http']['paths']]
        self.assertEqual(len(routes), 8); self.assertEqual(len(set(routes)), 8)
        self.assertEqual([i['metadata']['name'] for i in items if i['kind'] == 'Ingress'
                          and i['metadata']['name'] in manage.ROUTES
                          and manage.MIDDLEWARE_ANNOTATION in i['metadata']['annotations']], [manage.SUDOKU])

    def test_http_redirect_uses_web_entrypoint_and_https_middleware(self):
        items = manage.render(CONFIG)
        middleware = next(i for i in items if i['kind'] == 'Middleware'
                          and i['metadata']['name'] == manage.REDIRECT)
        ingress = next(i for i in items if i['kind'] == 'Ingress'
                       and i['metadata']['name'] == manage.HTTP_REDIRECT)
        self.assertEqual(middleware['spec'], {'redirectScheme': {'scheme': 'https', 'permanent': True}})
        self.assertEqual(ingress['metadata']['annotations'][
            'traefik.ingress.kubernetes.io/router.entrypoints'], 'web')
        self.assertEqual(ingress['metadata']['annotations'][manage.MIDDLEWARE_ANNOTATION],
                         'default-nicqx-redirect-https@kubernetescrd')
        self.assertNotIn('tls', ingress['spec'])

    def test_unrelated_paths_hosts_and_annotations_are_preserved(self):
        old = self.initial()
        main = next(i for i in old if i['metadata']['name'] == manage.MAIN)
        extra = manage.backend('/calendar', 'availability-calendar-service', 9090)
        main['spec']['rules'][0]['http']['paths'].append(copy.deepcopy(extra))
        other_host = {'host': 'other.example.org', 'http': {'paths': [manage.backend('/', 'untouched', 80)]}}
        main['spec']['rules'].append(copy.deepcopy(other_host))
        main['metadata']['annotations']['example.org/keep'] = 'yes'
        result = manage.render(CONFIG, old)
        updated = next(i for i in result if i['metadata']['name'] == manage.MAIN)
        self.assertIn(extra, updated['spec']['rules'][0]['http']['paths'])
        self.assertIn(other_host, updated['spec']['rules'])
        self.assertEqual(updated['metadata']['annotations'], main['metadata']['annotations'])

    def test_duplicate_legacy_ingress_is_reported_not_deleted(self):
        old = self.initial(); duplicate = copy.deepcopy(old[0]); duplicate['metadata']['name'] = 'main-ingress'
        with self.assertRaises(Failure): manage.render(CONFIG, old + [duplicate])

    def test_protected_backend_at_managed_path_is_never_replaced(self):
        old = self.initial()
        old[0]['spec']['rules'][0]['http']['paths'][0]['backend']['service']['name'] = 'availability-calendar-service'
        with self.assertRaises(Failure): manage.render(CONFIG, old)

    def test_existing_sudoku_middleware_is_reused(self):
        old = self.initial(); sudoku = next(i for i in old if i['metadata']['name'] == manage.SUDOKU)
        ref = 'default-original-strip@kubernetescrd'
        sudoku['metadata']['annotations'][manage.MIDDLEWARE_ANNOTATION] = ref
        result = manage.render(CONFIG, old, 'traefik.containo.us', {ref: {'spec': {'stripPrefix': {'prefixes': ['/sudoku']}}}})
        self.assertFalse(any(i['kind'] == 'Middleware' and i['metadata']['name'] == manage.STRIP
                             for i in result))
        self.assertEqual(next(i for i in result if i['metadata']['name'] == manage.SUDOKU)['metadata']['annotations'][manage.MIDDLEWARE_ANNOTATION], ref)

    def test_new_and_old_traefik_crd_discovery(self):
        for group in ['traefik.io', 'traefik.containo.us']:
            kube = Mock(); kube.get.return_value = {'items': [{'metadata': {'name': 'middlewares.'+group},
                              'spec': {'versions': [{'name': 'v1alpha1', 'served': True}]}}]}
            self.assertEqual(manage.discover_group(kube), group)

    def test_cert_manager_bootstrap_refuses_pi(self):
        kube = Mock(); kube.target = 'pi5'
        with self.assertRaises(Failure): manage.install_cert_manager(kube)
        kube.call.assert_not_called()


class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '10',
                        '-subj', '/CN=pmqxyz.hopto.org', '-addext', 'subjectAltName=DNS:pmqxyz.hopto.org',
                        '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem')],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        cls.secret = {'type': 'kubernetes.io/tls', 'data': {
            'tls.crt': base64.b64encode((root/'cert.pem').read_bytes()).decode(),
            'tls.key': base64.b64encode((root/'key.pem').read_bytes()).decode()}}

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_matching_certificate_and_key(self): manage.check_tls(self.secret, CONFIG['hostname'])

    def test_wrong_hostname_is_rejected(self):
        with self.assertRaises(Failure): manage.check_tls(self.secret, 'wrong.example.org')

    def test_wrong_key_is_rejected(self):
        secret = copy.deepcopy(self.secret); secret['data']['tls.key'] = base64.b64encode(b'invalid-key').decode()
        with self.assertRaises(Failure): manage.check_tls(secret, CONFIG['hostname'])


class GrandmaPublicationTests(unittest.TestCase):
    def test_grandma_bridge_targets_nuc_compose_port(self):
        items = manage.grandma_resources(CONFIG)
        by_kind = {item["kind"]: item for item in items}

        service = by_kind["Service"]
        self.assertEqual(service["metadata"]["name"], manage.GRANDMA_SERVICE)
        self.assertEqual(service["spec"]["ports"][0]["port"], 8090)
        self.assertNotIn("selector", service["spec"])

        endpoint_slice = by_kind["EndpointSlice"]
        self.assertEqual(
            endpoint_slice["metadata"]["labels"]["kubernetes.io/service-name"],
            manage.GRANDMA_SERVICE,
        )
        self.assertEqual(
            endpoint_slice["endpoints"][0]["addresses"],
            ["192.168.1.10"],
        )
        self.assertEqual(endpoint_slice["ports"][0]["port"], 8090)

        ingress = by_kind["Ingress"]
        rule = ingress["spec"]["rules"][0]
        self.assertEqual(rule["host"], CONFIG["hostname"])
        path = rule["http"]["paths"][0]
        self.assertEqual(path["path"], "/grandma")
        self.assertEqual(
            path["backend"]["service"]["name"],
            manage.GRANDMA_SERVICE,
        )
        self.assertEqual(
            ingress["spec"]["tls"][0]["secretName"],
            CONFIG["tls_secret"],
        )

    def test_grandma_bridge_refuses_foreign_route_owner(self):
        foreign = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": "foreign"},
            "spec": {
                "rules": [{
                    "host": CONFIG["hostname"],
                    "http": {
                        "paths": [
                            manage.backend("/grandma", "foreign-service", 80)
                        ]
                    },
                }]
            },
        }
        with self.assertRaises(Failure):
            manage.grandma_resources(CONFIG, [foreign])

    def test_grandma_backend_config_is_validated(self):
        configured = dict(CONFIG)
        configured["grandma_backend_ip"] = "192.168.1.10"
        configured["grandma_backend_port"] = 18090
        self.assertEqual(
            manage.grandma_backend(configured),
            ("192.168.1.10", 18090),
        )

        invalid = dict(CONFIG)
        invalid["grandma_backend_ip"] = "not-an-ip"
        with self.assertRaises(Failure):
            manage.grandma_backend(invalid)
