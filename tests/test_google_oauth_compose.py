# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GoogleOAuthComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (ROOT / "docker-compose.yml").read_text()
        cls.override = (ROOT / "docker-compose.override.yml.example").read_text()
        cls.dockerfile = (ROOT / "Dockerfile").read_text()
        cls.google_auth = (ROOT / "backend/tools/google_auth.py").read_text()

    def test_google_oauth_values_are_explicit_runtime_environment(self):
        self.assertIn(
            'GOOGLE_OAUTH_CLIENT_ID: "${GOOGLE_OAUTH_CLIENT_ID:-${GOOGLE_CLIENT_ID:-}}"',
            self.compose,
        )
        self.assertIn(
            'GOOGLE_OAUTH_CLIENT_SECRET: '
            '"${GOOGLE_OAUTH_CLIENT_SECRET:-${GOOGLE_CLIENT_SECRET:-}}"',
            self.compose,
        )
        self.assertIn(
            'GOOGLE_REDIRECT_URI: "${GOOGLE_REDIRECT_URI:-}"', self.compose
        )

    def test_backend_uses_canonical_runtime_names(self):
        for name in (
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_REDIRECT_URI",
        ):
            self.assertIn(name, self.google_auth)

    def test_secret_is_not_a_build_arg_or_image_label(self):
        build_block = self.compose.split("build:", 1)[1].split("container_name:", 1)[0]
        self.assertNotIn("GOOGLE", build_block)
        self.assertNotIn("CLIENT_SECRET", self.dockerfile)
        self.assertNotIn("GOOGLE", self.dockerfile)

    def test_full_env_file_is_not_exported(self):
        self.assertNotIn("env_file:", self.compose)
        self.assertNotIn("env_file:", self.override)

    def test_production_port_override_is_unchanged(self):
        self.assertIn("ports: !override", self.override)
        self.assertIn('"127.0.0.1:8080:80"', self.override)
        self.assertIn('"127.0.0.1:8088:443"', self.override)
        self.assertNotIn("environment:", self.override)
        self.assertNotIn("build:", self.override)


if __name__ == "__main__":
    unittest.main()
