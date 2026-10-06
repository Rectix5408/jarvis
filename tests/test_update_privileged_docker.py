# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PrivilegedDockerUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)],
            check=True, capture_output=True,
        )
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "master"], cwd=self.repo,
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Update Test"],
            cwd=self.repo, check=True,
        )
        for name in ("update.sh", "docker-compose.yml", "Dockerfile"):
            shutil.copy2(ROOT / name, self.repo / name)
        (self.repo / ".gitignore").write_text(".env\n")
        (self.repo / ".env").write_text("TEST_VALUE=fixture\n")
        (self.repo / ".env").chmod(0o600)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"], cwd=self.repo,
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(self.remote)],
            cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "master"], cwd=self.repo,
            check=True, capture_output=True,
        )
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._write_fake("docker", self._docker_script())
        self._write_fake("sudo", self._sudo_script())
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
prefix=""
[ "${FAKE_VIA_SUDO:-0}" = 1 ] && prefix="sudo "
printf '%sdocker %s\\n' "$prefix" "$*" >> "${FAKE_DOCKER_LOG}"
case "$*" in
  "info")
    if [ "${FAKE_VIA_SUDO:-0}" = 1 ]; then
      [ "${SUDO_DOCKER_OK:-1}" = 1 ]
    else
      [ "${DIRECT_DOCKER_OK:-1}" = 1 ]
    fi ;;
  "compose version") exit 0 ;;
  *" config --quiet") exit 0 ;;
  *" build jarvis") exit 0 ;;
  *" up -d --no-deps jarvis") exit 0 ;;
  *" ps -q jarvis") printf '%s\\n' fixture-container ;;
  *"State.Running"*"fixture-container") printf '%s\\n' true ;;
  *"image.revision"*"fixture-container")
    git -C "${FAKE_REPO}" rev-parse HEAD ;;
  *" exec -T jarvis curl "*) exit 0 ;;
  *) exit 1 ;;
esac
"""

    @staticmethod
    def _sudo_script():
        return """#!/bin/sh
set -eu
[ "${1:-}" = docker ] || exit 97
FAKE_VIA_SUDO=1 exec "$@"
"""

    def run_update(self, direct=True, sudo=True):
        log = self.root / "docker.log"
        env = os.environ.copy()
        env.update(
            PATH=f"{self.bin}:{env['PATH']}",
            FAKE_DOCKER_LOG=str(log),
            FAKE_REPO=str(self.repo),
            DIRECT_DOCKER_OK="1" if direct else "0",
            SUDO_DOCKER_OK="1" if sudo else "0",
            JARVIS_HEALTH_TIMEOUT="1",
            JARVIS_HEALTH_INTERVAL="0",
        )
        result = subprocess.run(
            ["bash", "update.sh"], cwd=self.repo, env=env,
            text=True, capture_output=True, timeout=10,
        )
        return result, log.read_text().splitlines() if log.exists() else []

    def test_direct_docker_is_preferred(self):
        result, log = self.run_update(direct=True, sudo=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(log)
        self.assertTrue(all(not line.startswith("sudo ") for line in log))

    def test_sudo_docker_is_used_for_every_operational_command(self):
        result, log = self.run_update(direct=False, sudo=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log[0], "docker info")
        operational = log[1:]
        self.assertTrue(operational)
        self.assertTrue(all(line.startswith("sudo docker ") for line in operational))
        joined = "\n".join(operational)
        for command in (
            "compose version", "config --quiet", "build jarvis",
            "up -d --no-deps jarvis", "inspect -f", "exec -T jarvis curl",
        ):
            self.assertIn(command, joined)

    def test_unreachable_docker_fails_before_compose(self):
        result, log = self.run_update(direct=False, sudo=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker-Daemon ist nicht erreichbar", result.stderr)
        self.assertEqual(log, ["docker info", "sudo docker info"])

    def test_git_is_never_run_through_sudo(self):
        script = (ROOT / "update.sh").read_text()
        self.assertNotIn("sudo git", script)
        result, _ = self.run_update(direct=False, sudo=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
