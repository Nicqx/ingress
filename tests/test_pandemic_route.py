"""Static guardrails for the separately applied Pandemic ingress."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PandemicIngressTests(unittest.TestCase):
    def test_invite_route_keeps_tls_and_removes_basic_auth_reference(self):
        config = (ROOT / "k8s/pandemic-ingress.yaml").read_text()
        self.assertIn("name: pandemic-game-ingress", config)
        self.assertIn("path: /pandemic", config)
        self.assertIn("pathType: Prefix", config)
        self.assertIn("name: pandemic-service", config)
        self.assertIn("number: 8111", config)
        self.assertIn("secretName: my-tls-secret", config)
        self.assertNotIn("stripPrefix:", config)
        self.assertNotIn("router.middlewares:", config)
        self.assertNotIn("basicAuth:", config)

    def test_publisher_requires_actual_invite_gate_before_apply(self):
        source = (ROOT / "scripts/publish-pandemic.sh").read_text()
        self.assertIn('if [[ "${1:-}" != "--apply" ]]', source)
        self.assertIn("rollout status deployment/pandemic", source)
        self.assertIn("get ingress -A -o json", source)
        self.assertIn('PANDEMIC_ACCESS_MODE") == "invite"', source)
        self.assertIn("APP INVITE GATE PASS", source)
        self.assertIn("e.code == 401", source)
        self.assertIn("apply -f k8s/pandemic-ingress.yaml", source)


if __name__ == "__main__":
    unittest.main()
