# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UpdateScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "master"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Update Test"], cwd=self.repo, check=True)
        for name in ("update.sh", "docker-compose.yml", "Dockerfile"):
            shutil.copy2(ROOT / name, self.repo / name)
        (self.repo / ".gitignore").write_text(".env\ndata/\ndocker-compose.override.yml\n")
        (self.repo / ".env").write_text("TEST_SECRET=must-not-appear\n")
        (self.repo / ".env").chmod(0o600)
        (self.repo / "data").mkdir()
        (self.repo / "data" / "token.json").write_text("persistent-token-fixture")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(self.remote)], cwd=self.repo, check=True)
        subprocess.run(["git", "push", "-u", "origin", "master"], cwd=self.repo, check=True, capture_output=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._write_fake("docker", self._docker_script())
        self._write_fake("curl", "#!/bin/sh\nexit 0\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_fake(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _docker_script():
        return """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "${FAKE_DOCKER_LOG}"
case "$*" in
  "info") exit 0 ;;
  "compose version") exit 0 ;;
  *" config --quiet") [ "${FAIL_CONFIG:-0}" = 0 ] ;;
  *" build jarvis") [ "${FAIL_BUILD:-0}" = 0 ] ;;
  *" up -d --no-deps jarvis") [ "${FAIL_UP:-0}" = 0 ] ;;
  *" ps -q jarvis") printf '%s\\n' fixture-container ;;
  *"State.Running"*"fixture-container")
    [ "${FAIL_CONTAINER:-0}" = 0 ] && printf '%s\\n' true || printf '%s\\n' false ;;
  *"image.revision"*"fixture-container")
    git -C "${FAKE_REPO}" rev-parse HEAD ;;
  *" exec -T jarvis curl "*) [ "${FAIL_HEALTH:-0}" = 0 ] ;;
  *) exit 1 ;;
esac
"""

    def run_update(self, **extra):
        log = self.root / "docker.log"
        env = os.environ.copy()
        env.update(
            PATH=f"{self.bin}:{env['PATH']}",
            FAKE_DOCKER_LOG=str(log),
            FAKE_REPO=str(self.repo),
            JARVIS_HEALTH_TIMEOUT="1",
            JARVIS_HEALTH_INTERVAL="0",
        )
        env.update({key: str(value) for key, value in extra.items()})
        result = subprocess.run(
            ["bash", "update.sh"], cwd=self.repo, env=env,
            text=True, capture_output=True, timeout=10,
        )
        return result, log.read_text() if log.exists() else ""

    def test_no_update_preserves_env_data_and_only_updates_jarvis(self):
        env_before = (self.repo / ".env").read_bytes()
        data_before = (self.repo / "data" / "token.json").read_bytes()
        result, docker_log = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Status: SUCCESS", result.stdout)
        self.assertEqual((self.repo / ".env").read_bytes(), env_before)
        self.assertEqual((self.repo / "data" / "token.json").read_bytes(), data_before)
        self.assertIn("build jarvis", docker_log)
        self.assertIn("up -d --no-deps jarvis", docker_log)
        self.assertNotIn("ollama", docker_log.lower())
        self.assertNotIn("must-not-appear", result.stdout + result.stderr + docker_log)

    def test_dirty_worktree_fails_before_docker(self):
        (self.repo / "Dockerfile").write_text("dirty")
        result, docker_log = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Arbeitsbaum ist nicht sauber", result.stderr)
        self.assertEqual(docker_log, "")

    def test_build_failure_is_fatal(self):
        result, docker_log = self.run_update(FAIL_BUILD=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Image konnte nicht gebaut", result.stderr)
        self.assertNotIn("up -d", docker_log)

    def test_invalid_compose_config_is_fatal(self):
        result, docker_log = self.run_update(FAIL_CONFIG=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Compose-Konfiguration ist ungueltig", result.stderr)
        self.assertNotIn("build jarvis", docker_log)

    def test_container_failure_is_fatal(self):
        result, _ = self.run_update(FAIL_CONTAINER=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Container laeuft nicht", result.stderr)

    def test_health_timeout_is_fatal(self):
        result, _ = self.run_update(FAIL_HEALTH=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Health-Check", result.stderr)

    def test_env_permissions_are_enforced(self):
        (self.repo / ".env").chmod(0o644)
        result, docker_log = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chmod 600 .env", result.stderr)
        self.assertEqual(docker_log, "")

    def test_build_context_excludes_secrets_and_runtime_data(self):
        ignored = (ROOT / ".dockerignore").read_text()
        for pattern in (
            ".env", "docker-compose.override.yml", "data/google_auth/",
            "data/local_ai/", "services/whatsapp-bridge/auth/",
        ):
            self.assertIn(pattern, ignored)

    def test_override_uses_loopback_and_replaces_base_ports(self):
        override = (ROOT / "docker-compose.override.yml.example").read_text()
        self.assertIn("ports: !override", override)
        self.assertIn('"127.0.0.1:8080:80"', override)
        self.assertIn('"127.0.0.1:8088:443"', override)

    def test_script_has_no_destructive_git_or_unscoped_compose_commands(self):
        script = (ROOT / "update.sh").read_text()
        self.assertNotIn("git stash", script)
        self.assertNotIn("git reset --hard", script)
        self.assertIn('compose build "$SERVICE"', script)
        self.assertIn('compose up -d --no-deps "$SERVICE"', script)


if __name__ == "__main__":
    unittest.main()
