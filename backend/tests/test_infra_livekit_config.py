"""Static checks for infra/livekit: no secrets committed and no config LiveKit cannot read.

Found by running LiveKit v1.13.11: it does not expand ${VARIABLES} inside its YAML file, and
`webhook.api_key: ${LIVEKIT_API_KEY}` makes the server refuse to start. Docker Compose, by contrast,
does interpolate ${VARIABLES} in docker-compose.yml, which is where the secret is injected.
"""
import re
import unittest
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "infra" / "livekit"


class LiveKitInfraConfigTests(unittest.TestCase):
    def setUp(self):
        self.yaml = (INFRA / "livekit.yaml").read_text(encoding="utf-8")
        self.compose = (INFRA / "docker-compose.yml").read_text(encoding="utf-8")
        self.env_example = (INFRA / ".env.example").read_text(encoding="utf-8")

    def code(self, text):
        return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))

    def test_livekit_yaml_has_no_variable_placeholders(self):
        self.assertNotIn("${", self.code(self.yaml))

    def test_webhook_key_is_the_public_key_identifier_from_env_example(self):
        match = re.search(r"^LIVEKIT_API_KEY=(\S+)", self.env_example, re.MULTILINE)
        self.assertIsNotNone(match, ".env.example must define the key identifier")
        webhook = re.search(r"^webhook:\s*\n\s+api_key:\s*(\S+)", self.code(self.yaml), re.MULTILINE)
        self.assertIsNotNone(webhook, "the webhook block must set api_key")
        self.assertEqual(webhook.group(1), match.group(1))

    def test_no_secret_is_committed(self):
        self.assertRegex(self.env_example, r"(?m)^LIVEKIT_API_SECRET=\s*$")
        for text in (self.yaml, self.compose, self.env_example):
            self.assertNotRegex(self.code(text), r"(?i)secret[ \t]*[:=][ \t]*[A-Za-z0-9+/_-]{16,}")

    def test_secrets_reach_the_containers_only_through_compose_interpolation(self):
        self.assertIn('LIVEKIT_KEYS: "${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}"', self.compose)
        self.assertIn("api_secret: ${LIVEKIT_API_SECRET}", self.compose)

    def test_no_loopback_node_ip_which_would_break_egress_ice(self):
        # Found by the first real recording: Egress is a Chrome in a container, so a LiveKit
        # candidate of 127.0.0.1 points back at Egress itself and ICE never completes
        # ("participant_connection_aborted", then Egress "Start signal not received").
        self.assertNotRegex(self.code(self.yaml), r"(?m)^\s*node_ip:\s*(127\.|localhost|::1)")
        self.assertNotRegex(self.code(self.compose), r"(?i)node_ip")

    def test_egress_reaches_livekit_over_the_internal_network_not_loopback_or_a_public_name(self):
        self.assertIn("ws_url: ws://livekit:7880", self.compose)

    def test_the_networking_guide_documents_the_staging_and_production_approach(self):
        guide = (INFRA / "NETWORKING.md").read_text(encoding="utf-8")
        for needle in ("node_ip: 127.0.0.1", "participant_connection_aborted", "Start signal not received",
                       "## Staging", "## Production", "use_external_ip", "TURN", "room_composite_cpu_cost",
                       "32+ characters"):
            self.assertIn(needle, guide, needle)
        self.assertIn("NETWORKING.md", (INFRA / "README.md").read_text(encoding="utf-8"))

    def test_published_ports_stay_on_loopback(self):
        ports = re.findall(r'^\s*-\s*"([^"]*:\d+(?:-\d+)?:\d+(?:-\d+)?(?:/udp)?)"', self.compose, re.MULTILINE)
        self.assertTrue(ports)
        for mapping in ports:
            self.assertTrue(mapping.startswith("127.0.0.1:"), mapping)


if __name__ == "__main__":
    unittest.main()
