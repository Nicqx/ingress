"""Static guardrails for the separately applied Pandemic ingress."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PandemicIngressTests(unittest.TestCase):
    def test_route_is_basic_auth_protected_and_keeps_prefix(self):
        config = (ROOT / "k8s/pandemic-ingress.yaml").read_text()
        self.assertIn("name: pandemic-auth", config)
        self.assertIn("basicAuth:", config)
        self.assertIn("secret: pandemic-basic-auth", config)
        self.assertIn("removeHeader: true", config)
        self.assertIn("name: pandemic-game-ingress", config)
        self.assertIn("default-pandemic-auth@kubernetescrd", config)
        self.assertIn("path: /pandemic", config)
        self.assertIn("pathType: Prefix", config)
        self.assertIn("name: pandemic-service", config)
        self.assertIn("number: 8111", config)
        self.assertIn("secretName: my-tls-secret", config)
        self.assertNotIn("stripPrefix:", config)

    def test_publisher_checks_auth_and_backend_before_apply(self):
        source = (ROOT / "scripts/publish-pandemic.sh").read_text()
        self.assertIn('if [[ "${1:-}" != "--apply" ]]', source)
        self.assertIn("get secret pandemic-basic-auth", source)
        self.assertIn("rollout status deployment/pandemic", source)
        self.assertIn("get ingress -A -o json", source)
        self.assertIn("kubectl delete", (ROOT / "PANDEMIC.md").read_text())


if __name__ == "__main__":
    unittest.main()
