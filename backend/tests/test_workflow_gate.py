"""Static guard against accidentally bypassing the PostgreSQL quality job."""

import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
DEPLOY_SCRIPT = Path(__file__).resolve().parents[2] / "deploy.sh"


class TestWorkflowGate(unittest.TestCase):
    def test_deploy_requires_postgresql_quality_and_push_on_main(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("\n  quality:\n", source)
        self.assertIn("\n  deploy:\n", source)
        quality, deploy = source.split("\n  deploy:\n", 1)
        self.assertIn("image: postgres:16-alpine", quality)
        self.assertIn("python tests/prepare_postgres_qa.py", quality)
        self.assertIn("python -m unittest discover -s tests -q", quality)
        self.assertIn("needs: quality", deploy)
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", deploy)
        self.assertIn("ARCK_EXPECTED_SHA: ${{ github.sha }}", deploy)
        self.assertIn('cp deploy.sh "$RUNNER_TEMP/arckenites-deploy.sh"', deploy)
        self.assertIn('"$NEW_APP_SHA" != "$ARCK_EXPECTED_SHA"',
                      DEPLOY_SCRIPT.read_text(encoding="utf-8"))
