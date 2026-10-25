"""HTTP bind safety: the unauthenticated local API must stay loopback-only."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.server import resolve_host


class ServerBindFixtures(unittest.TestCase):
    def test_explicit_loopback_variants_are_accepted(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertEqual(resolve_host(host), host)

    def test_empty_host_defaults_to_loopback(self):
        self.assertEqual(resolve_host(""), "127.0.0.1")

    def test_non_loopback_requires_explicit_opt_in(self):
        with mock.patch.dict(os.environ, {"MOYU_ALLOW_REMOTE": "0"}, clear=False):
            with self.assertRaises(SystemExit):
                resolve_host("0.0.0.0")

    def test_non_loopback_with_opt_in_is_allowed(self):
        with mock.patch.dict(os.environ, {"MOYU_ALLOW_REMOTE": "1"}, clear=False):
            self.assertEqual(resolve_host("0.0.0.0"), "0.0.0.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
