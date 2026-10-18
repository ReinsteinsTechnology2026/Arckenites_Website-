import sys
import unittest
from unittest.mock import patch

import seed


class SeedSafetyTests(unittest.TestCase):
    def test_full_seed_refuses_production_before_database_connection(self):
        with patch.object(sys, "argv", ["seed.py"]), \
             patch.object(seed.settings, "env", "production"), \
             patch.object(seed, "SessionLocal") as connect:
            with self.assertRaisesRegex(SystemExit, "disabled in production"):
                seed.main()
            connect.assert_not_called()

    def test_full_seed_refuses_placeholder_admin_password(self):
        with patch.object(sys, "argv", ["seed.py"]), \
             patch.object(seed.settings, "env", "development"), \
             patch.object(seed.settings, "seed_admin1_password", "change-me"), \
             patch.object(seed, "SessionLocal") as connect:
            with self.assertRaisesRegex(SystemExit, "non-placeholder"):
                seed.main()
            connect.assert_not_called()
