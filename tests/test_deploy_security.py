# SPDX-License-Identifier: Apache-2.0
import ast
import hashlib
import hmac
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeploymentSecurityTests(unittest.TestCase):
    def test_docker_password_fails_closed(self):
        tree = ast.parse((ROOT / "backend/main.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "authenticate_linux_user")
        namespace = {"ALLOWED_USERS": {"jarvis"}, "_DOCKER_MODE": True, "_JARVIS_PASSWORD": "",
                     "_load_auth_state": lambda: {}, "hashlib": hashlib, "hmac": hmac}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "auth", "exec"), namespace)
        authenticate = namespace["authenticate_linux_user"]
        self.assertFalse(authenticate("jarvis", ""))
        self.assertFalse(authenticate("jarvis", "jarvis"))
        namespace["_JARVIS_PASSWORD"] = "fixture-only-password"
        self.assertTrue(authenticate("jarvis", "fixture-only-password"))
        namespace["_load_auth_state"] = lambda: {"docker_password": {"jarvis": hashlib.sha256(b"saved-password").hexdigest()}}
        self.assertTrue(authenticate("jarvis", "saved-password"))
        self.assertFalse(authenticate("jarvis", "fixture-only-password"))

    def test_no_default_password_or_global_tls_bypass(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        entrypoint = (ROOT / "docker/entrypoint.sh").read_text()
        self.assertIn('${JARVIS_PASSWORD:?', compose)
        self.assertNotIn('NODE_TLS_REJECT_UNAUTHORIZED:', compose)
        self.assertNotIn('export NODE_TLS_REJECT_UNAUTHORIZED=0', entrypoint)
        self.assertIn('unset NODE_TLS_REJECT_UNAUTHORIZED', entrypoint)
        self.assertIn('NODE_EXTRA_CA_CERTS', entrypoint)
        for file in ("backend/main.py", "backend/config.py"):
            self.assertNotIn('os.getenv("JARVIS_PASSWORD", "jarvis")', (ROOT / file).read_text())

    def test_node_trusts_local_cert_but_rejects_untrusted_cert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = root / "openssl.cnf"
            cfg.write_text('[req]\ndistinguished_name=dn\nx509_extensions=extensions\nprompt=no\n[dn]\nCN=localhost\n[extensions]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n')
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-config", str(cfg), "-keyout", str(root / "key.pem"), "-out", str(root / "cert.pem")], check=True, capture_output=True)
            script = '''
const https = require('node:https'), fs = require('node:fs');
const server = https.createServer({ key: fs.readFileSync(process.env.TEST_KEY), cert: fs.readFileSync(process.env.TEST_CERT) }, (_, res) => res.end('OK'));
server.listen(0, '127.0.0.1', async () => {
  let trusted = false;
  try { const res = await fetch(`https://127.0.0.1:${server.address().port}`); trusted = await res.text() === 'OK'; } catch (_) {}
  server.closeAllConnections(); server.close();
  if (trusted !== (process.env.EXPECT_TRUST === '1')) process.exitCode = 1;
});
'''
            environment = {key: value for key, value in os.environ.items() if key not in {"NODE_TLS_REJECT_UNAUTHORIZED", "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS"}}
            environment.update(TEST_KEY=str(root / "key.pem"), TEST_CERT=str(root / "cert.pem"))
            subprocess.run(["node", "-e", script], env={**environment, "EXPECT_TRUST": "0"}, check=True, timeout=20, capture_output=True)
            subprocess.run(["node", "-e", script], env={**environment, "EXPECT_TRUST": "1", "NODE_EXTRA_CA_CERTS": str(root / "cert.pem")}, check=True, timeout=20, capture_output=True)


if __name__ == "__main__":
    unittest.main()
