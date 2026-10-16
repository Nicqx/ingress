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
    def test_grandma_ingress_targets_cluster_service(self):
        items = manage.grandma_resources(CONFIG)
        self.assertEqual(len(items), 1)
        ingress = items[0]
        self.assertEqual(ingress["kind"], "Ingress")
        self.assertEqual(ingress["metadata"]["name"], manage.GRANDMA_INGRESS)

        rule = ingress["spec"]["rules"][0]
        self.assertEqual(rule["host"], CONFIG["hostname"])
        path = rule["http"]["paths"][0]
        self.assertEqual(path["path"], "/grandma")
        self.assertEqual(
            path["backend"]["service"]["name"],
            manage.GRANDMA_SERVICE,
        )
        self.assertEqual(
            path["backend"]["service"]["port"]["number"],
            8090,
        )
        self.assertEqual(
            ingress["spec"]["tls"][0]["secretName"],
            CONFIG["tls_secret"],
        )

    def test_grandma_ingress_refuses_foreign_route_owner(self):
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

    def test_grandma_service_check_accepts_ready_endpoint(self):
        kube = Mock()
        kube.get.return_value = {
            "spec": {
                "ports": [{"port": 8090}]
            }
        }
        kube.call.return_value = json.dumps({
            "items": [{
                "endpoints": [{
                    "addresses": ["10.42.0.10"],
                    "conditions": {"ready": True},
                }]
            }]
        }).encode()

        manage.check_grandma_service(kube)

    def test_grandma_service_check_rejects_missing_service(self):
        kube = Mock()
        kube.get.return_value = None
        with self.assertRaises(Failure):
            manage.check_grandma_service(kube)


class MunkaidoPublicationTests(unittest.TestCase):
    def test_munkaido_ingress_strips_prefix_and_targets_service(self):
        items = manage.munkaido_resources(CONFIG)
        self.assertEqual(len(items), 2)
        middleware = next(item for item in items if item["kind"] == "Middleware")
        ingress = next(item for item in items if item["kind"] == "Ingress")

        self.assertEqual(middleware["metadata"]["name"], manage.MUNKAIDO_STRIP)
        self.assertEqual(
            middleware["spec"],
            {"stripPrefix": {"prefixes": ["/munkaido"]}},
        )
        self.assertEqual(ingress["metadata"]["name"], manage.MUNKAIDO_INGRESS)
        self.assertEqual(
            ingress["metadata"]["annotations"][manage.MIDDLEWARE_ANNOTATION],
            "default-munkaido-strip-prefix@kubernetescrd",
        )
        path = ingress["spec"]["rules"][0]["http"]["paths"][0]
        self.assertEqual(path["path"], "/munkaido")
        self.assertEqual(path["backend"]["service"]["name"], "munkaido-nyilvantarto")
        self.assertEqual(path["backend"]["service"]["port"]["number"], 8000)
        self.assertEqual(
            ingress["spec"]["tls"][0]["secretName"],
            CONFIG["tls_secret"],
        )

    def test_munkaido_ingress_refuses_foreign_route_owner(self):
        foreign = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": "foreign"},
            "spec": {"rules": [{
                "host": CONFIG["hostname"],
                "http": {"paths": [
                    manage.backend("/munkaido", "foreign-service", 80)
                ]},
            }]},
        }
        with self.assertRaises(Failure):
            manage.munkaido_resources(CONFIG, [foreign])

    def test_munkaido_service_check_accepts_ready_endpoint(self):
        kube = Mock()
        kube.get.return_value = {"spec": {"ports": [{"port": 8000}]}}
        kube.call.return_value = json.dumps({
            "items": [{"endpoints": [{
                "addresses": ["10.42.0.20"],
                "conditions": {"ready": True},
            }]}]
        }).encode()
        manage.check_munkaido_service(kube)

    def test_munkaido_service_check_rejects_missing_service(self):
        kube = Mock()
        kube.get.return_value = None
        with self.assertRaises(Failure):
            manage.check_munkaido_service(kube)



class WizardPublicationTests(unittest.TestCase):
    def test_wizard_ingress_targets_nuc_service_and_retains_prefix(self):
        items = manage.wizard_resources(CONFIG)
        self.assertEqual(len(items), 1)
        ingress = items[0]
        self.assertEqual(ingress['metadata']['name'], manage.WIZARD_INGRESS)
        self.assertEqual(ingress['spec']['ingressClassName'], 'traefik')
        self.assertNotIn(manage.MIDDLEWARE_ANNOTATION,
                         ingress['metadata'].get('annotations', {}))
        rule = ingress['spec']['rules'][0]
        self.assertEqual(rule['host'], CONFIG['hostname'])
        routes = rule['http']['paths']
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0], manage.backend('/wizard-of-wor',
                                                 'wizard-of-wor-service', 8107))
        self.assertEqual(ingress['spec']['tls'], [{
            'hosts': [CONFIG['hostname']],
            'secretName': CONFIG['tls_secret'],
        }])
        self.assertIn(('Ingress', manage.WIZARD_INGRESS), manage.ALLOWED)

    def test_wizard_ingress_refuses_foreign_owner(self):
        foreign = {
            'metadata': {'name': 'other-ingress'},
            'spec': {'rules': [{
                'host': CONFIG['hostname'],
                'http': {'paths': [manage.backend(
                    '/wizard-of-wor', 'foreign-service', 7000,
                )]},
            }]},
        }
        with self.assertRaises(Failure):
            manage.wizard_resources(CONFIG, [foreign])

    def test_wizard_refuses_changed_existing_own_route(self):
        original = manage.wizard_resources(CONFIG)[0]
        changed = copy.deepcopy(original)
        changed['spec']['rules'][0]['http']['paths'][0]['backend']['service']['name'] = 'other-service'
        with self.assertRaises(Failure):
            manage.wizard_resources(CONFIG, [changed])
        changed = copy.deepcopy(original)
        changed['metadata']['annotations'][manage.MIDDLEWARE_ANNOTATION] = 'foreign-strip'
        with self.assertRaises(Failure):
            manage.wizard_resources(CONFIG, [changed])

    def test_wizard_check_accepts_ready_service(self):
        kube = Mock()
        kube.get.return_value = {'spec': {'ports': [
            {'port': 8107, 'protocol': 'TCP'},
        ]}}
        kube.call.return_value = json.dumps({'items': [{
            'ports': [{'port': 8107, 'protocol': 'TCP'}],
            'endpoints': [{
                'addresses': ['10.42.1.11'],
                'conditions': {'ready': True},
            }],
        }]}).encode()
        manage.check_wizard_service(kube)
        kube.get.assert_called_with('service', 'wizard-of-wor-service')

    def test_wizard_check_refuses_missing_or_wrong_service(self):
        kube = Mock()
        kube.get.return_value = None
        with self.assertRaises(Failure):
            manage.check_wizard_service(kube)
        kube.get.return_value = {'spec': {'ports': [{'port': 8099}]}}
        with self.assertRaises(Failure):
            manage.check_wizard_service(kube)

    def test_wizard_check_refuses_unready_or_wrong_endpoint(self):
        kube = Mock()
        kube.get.return_value = {'spec': {'ports': [
            {'port': 8107, 'protocol': 'TCP'},
        ]}}
        for port, is_ready in ((8107, False), (9999, True)):
            kube.call.return_value = json.dumps({'items': [{
                'ports': [{'port': port, 'protocol': 'TCP'}],
                'endpoints': [{
                    'addresses': ['10.42.1.11'],
                    'conditions': {'ready': is_ready},
                }],
            }]}).encode()
            with self.assertRaises(Failure):
                manage.check_wizard_service(kube)
