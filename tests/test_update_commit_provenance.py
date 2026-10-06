# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import re
import subprocess
import unittest

from tests import test_update_privileged_docker as privileged_docker


ROOT = Path(__file__).resolve().parents[1]


class UpdateCommitProvenanceTests(unittest.TestCase):
    def test_sudo_build_receives_exact_git_head(self):
        harness = privileged_docker.PrivilegedDockerUpdateTests(
            methodName="test_direct_docker_is_preferred"
        )
        harness.setUp()
        try:
            expected = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=harness.repo, text=True
            ).strip()
            result, log = harness.run_update(direct=False, sudo=True)
        finally:
            harness.tearDown()

        self.assertEqual(result.returncode, 0, result.stderr)
        build_lines = [line for line in log if " compose " in line and " build " in line]
        self.assertEqual(len(build_lines), 1)
        self.assertIn(f"--build-arg JARVIS_COMMIT={expected}", build_lines[0])
        self.assertNotIn("JARVIS_COMMIT=unknown", build_lines[0])

    def test_compose_has_controlled_unknown_fallback(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn('JARVIS_COMMIT: "${JARVIS_COMMIT:-unknown}"', compose)

    def test_dockerfile_revision_label_uses_build_arg(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertRegex(dockerfile, r"ARG JARVIS_COMMIT=unknown")
        self.assertIn('org.opencontainers.image.revision="${JARVIS_COMMIT}"', dockerfile)

    def test_updater_sources_and_verifies_commit_locally(self):
        script = (ROOT / "update.sh").read_text()
        self.assertIn('target_commit="$(git rev-parse HEAD)"', script)
        self.assertIn('JARVIS_COMMIT=$target_commit', script)
        self.assertIn('[[ "$running_commit" == "$target_commit" ]]', script)
        assignments = re.findall(r"target_commit=([^\n]+)", script)
        self.assertEqual(assignments, ['"$(git rev-parse HEAD)"'])

    def test_production_port_override_is_unchanged(self):
        override = (ROOT / "docker-compose.override.yml.example").read_text()
        self.assertIn("ports: !override", override)
        self.assertIn('"127.0.0.1:8080:80"', override)
        self.assertIn('"127.0.0.1:8088:443"', override)
        self.assertNotIn("build:", override)
        self.assertNotIn("image:", override)
        self.assertNotIn("env_file:", override)


if __name__ == "__main__":
    unittest.main()
