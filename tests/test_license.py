#!/usr/bin/env python3
"""Modified in Reinhold-Jesse/jarvis on 2026-10-04: license-free HTTP regressions.

Run original handlers and auth dependencies in FastAPI without importing main
or config (which migrate live settings/start services). Service boundaries are
substituted; HMAC auth, HTTP dependencies, validation and upload loops are real.
All test data stays in a temporary directory. Run: python3 tests/test_license.py
Requires the project's fastapi, httpx and python-multipart dependencies.
"""
import ast
import asyncio
import copy
import hashlib
import hmac
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
TREE = ast.parse((ROOT / "backend/main.py").read_text(encoding="utf-8"))
FUNCTIONS = {n.name: n for n in TREE.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
sys.path.insert(0, str(ROOT))


def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class LocalFeatures(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="jarvis_features_")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.folder = root / "data/knowledge"
        self.folder.mkdir(parents=True)
        for i in range(120):
            (self.folder / f"existing-{i}.md").write_text("existing knowledge")
        self.profiles = [{"id": "p0", "name": "first"}]
        self.skills = {f"existing-{i}" for i in range(20)}
        self.users, self.jobs, self.assignments, self.restarts = [], {}, {}, []
        self.settings = {"ad_server": "directory", "ad_domain": "example"}
        self.blocked, self.denied_logins, self.must_change, self.totp = set(), set(), set(), set()
        self.revoked = {}
        self.rate_allowed, self.reloads = True, 0

        def create_profile(data):
            p = {**data, "id": f"p{len(self.profiles)}"}
            self.profiles.append(p)
            return p

        def enable_skill(name):
            self.skills.add(name)
            return {"success": True}

        def reload_tools():
            self.reloads += 1

        def add_job(**data):
            self.jobs[data["job_id"]] = data

        kn = module("backend.tools.knowledge", PROJECT_ROOT=root,
                    _get_folders=lambda: [self.folder], invalidate_files_cache=lambda: None)
        for key in ("TEXT", "PDF", "DOCX", "XLSX", "PPTX", "VIDEO", "AUDIO", "IMAGE"):
            setattr(kn, "EXTENSIONS_" + key, {".md"} if key == "TEXT" else set())
        up = module("backend.update_manager", apply_update=lambda: {"ok": True},
                    restart_service_delayed=lambda **kw: self.restarts.append(kw))
        sc = module("backend.scheduler", cron_manager=types.SimpleNamespace(
            delete_job=lambda key: self.jobs.pop(key, None), add_job=add_job))
        kg = module("backend.knowledge_groups", set_assignment=lambda p, g:
                    self.assignments.__setitem__(p, g))
        modules = {m.__name__: m for m in (kn, up, sc, kg)}
        modules.update({"backend.license": None, "backend.license_enforce": None})
        p = patch.dict(sys.modules, modules)
        p.start()
        self.addCleanup(p.stop)
        cfg = types.SimpleNamespace(
            SECRET_KEY="test-only-hmac-secret", profiles=self.profiles,
            create_profile=create_profile,
            get_setting=lambda k, default=None: self.settings.get(k, default),
            save_setting=lambda k, v: self.settings.__setitem__(k, v))
        self.ns = dict(
            app=FastAPI(), asyncio=asyncio, config=cfg, Path=Path, hmac=hmac,
            hashlib=hashlib, time=time, Depends=Depends, File=File, Form=Form,
            Request=Request, UploadFile=UploadFile, HTTPException=HTTPException,
            JSONResponse=JSONResponse, ALLOWED_USERS={"jarvis"}, _DOCKER_MODE=True,
            _revoked_logins=self.revoked, _norm_login=lambda u: u.lower(),
            _user_is_admin=lambda u: u == "jarvis", _is_admin_user=lambda u: u == "jarvis",
            _user_must_change=lambda u: u in self.must_change,
            _login_still_allowed=lambda u: u not in self.denied_logins,
            _note_activity=lambda *a: None, _may_edit_knowledge=lambda u: u == "editor",
            _check_rate_limit=lambda ip: self.rate_allowed,
            _record_login_attempt=lambda ip: None,
            authenticate_linux_user=lambda u, password, details: password == "correct-password",
            _get_user_auth_state=lambda u: {"totp_enabled": u in self.totp,
                                             "totp_secret": "JBSWY3DPEHPK3PXP"},
            _display_name=lambda u: u,
            _user_sessions=types.SimpleNamespace(record_login=lambda u, *a, **kw:
                                                 self.users.append(u)),
            security_guard=types.SimpleNamespace(
                is_blocked=lambda u: u in self.blocked,
                get_block=lambda u: {"reason": "blocked"} if u in self.blocked else None),
            _get_skill_manager=lambda: types.SimpleNamespace(enable_skill=enable_skill),
            _reload_agent_tools=reload_tools,
            _kb_mirror_guard=lambda f: JSONResponse({"error": "mirror"}, 403)
                if f == "data/mirror" else None,
            _kb_configured_root_for=lambda f: "data/knowledge" if f == "data/knowledge" else None,
            _kb_safe_within_data=lambda f: self.folder if f == "data/knowledge" else None,
            _kb_norm_rel=lambda f: f,
            _wissen_check_groups=lambda u, g: (u == "editor" and g == ["own"], "no access"),
            _editable_groups_for=lambda u: [{"id": "own"}],
            _wissen_allowed_folders=lambda u, g: ["data/knowledge"],
        )
        names = ("generate_token", "verify_token", "require_auth", "require_local_auth",
                 "require_knowledge_editor", "login", "create_profile", "enable_skill",
                 "update_apply", "update_settings_set", "upload_knowledge_files", "wissen_upload")
        tree = ast.fix_missing_locations(ast.Module(
            body=[copy.deepcopy(FUNCTIONS[n]) for n in names], type_ignores=[]))
        exec(compile(tree, str(ROOT / "backend/main.py"), "exec"), self.ns)
        self.client = TestClient(self.ns["app"])
        self.addCleanup(self.client.close)

    def headers(self, user="jarvis"):
        return {"Authorization": "Bearer " + self.ns["generate_token"](user)}

    def test_profiles_above_free_and_basic_limit(self):
        for i in range(12):
            r = self.client.post("/api/profiles", json={"name": f"profile-{i}"}, headers=self.headers())
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["success"])
        self.assertEqual(len(self.profiles), 13)

    def test_skills_above_five_and_tool_reload(self):
        r = self.client.post("/api/skills/another/enable", headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.skills), 21)
        self.assertEqual(self.reloads, 1)

    def test_more_than_ten_authenticated_users(self):
        for i in range(20):
            r = self.client.post("/api/login", json={"username": f"user-{i}",
                                                       "password": "correct-password"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["success"])
            self.assertEqual(self.ns["verify_token"](r.json()["token"]), f"user-{i}")
        self.assertEqual(len(set(self.users)), 20)

    def test_both_upload_paths_above_fifty_and_hundred_files(self):
        for route in ("/api/knowledge/upload", "/api/wissen/upload"):
            r = self.client.post(route, headers=self.headers("editor"),
                                 data={"folder": "data/knowledge", "groups": "own"},
                                 files=[("files", (f"new-{i}.md", b"knowledge", "text/plain"))
                                        for i in range(60)])
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["total_saved"], 60)
        self.assertEqual(len(list(self.folder.glob("*.md"))), 240)
        self.assertTrue(self.assignments)

    def test_manual_and_scheduled_updates(self):
        r = self.client.post("/api/update/apply", headers=self.headers())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(len(self.restarts), 1)
        for schedule in ("daily", "weekly"):
            r = self.client.post("/api/update/settings", headers=self.headers(),
                                 json={"auto_update_schedule": schedule})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.jobs["system_auto_update"]["owner"], "jarvis")
            self.assertTrue(self.jobs["system_auto_update"]["owner_privileged"])
        self.assertEqual(self.client.post("/api/update/settings", headers=self.headers(),
                         json={"auto_update_schedule": "invalid"}).status_code, 400)
        self.client.post("/api/update/settings", headers=self.headers(),
                         json={"auto_update_schedule": "never"})
        self.assertFalse(self.jobs)

    def test_admin_actions_still_require_auth_and_admin(self):
        for route in ("/api/profiles", "/api/skills/another/enable",
                      "/api/update/apply", "/api/update/settings"):
            with self.subTest(route=route):
                self.assertEqual(self.client.post(route, json={}).status_code, 401)
                self.assertEqual(self.client.post(route, json={},
                                 headers=self.headers("reader")).status_code, 403)
        self.assertEqual(len(self.profiles), 1)
        self.assertEqual(len(self.skills), 20)
        self.assertFalse(self.restarts)

    def test_invalid_credentials_and_two_factor_still_block_login(self):
        self.assertEqual(self.client.post("/api/login", json={"username": "user",
                         "password": "wrong"}).status_code, 401)
        self.totp.add("user")
        r = self.client.post("/api/login", json={"username": "user", "password": "correct-password"})
        self.assertTrue(r.json()["requires_totp"])
        self.assertNotIn("token", r.json())
        self.rate_allowed = False
        self.assertEqual(self.client.post("/api/login", json={}).status_code, 429)
        self.assertFalse(self.users)

    def test_password_change_revocation_and_access_removal(self):
        headers = self.headers()
        self.must_change.add("jarvis")
        self.assertEqual(self.client.post("/api/profiles", json={}, headers=headers).status_code, 403)
        self.must_change.clear()
        self.denied_logins.add("jarvis")
        self.assertEqual(self.client.post("/api/profiles", json={}, headers=headers).status_code, 403)
        self.denied_logins.clear()
        self.revoked["jarvis"] = int(time.time())
        self.assertEqual(self.client.post("/api/profiles", json={}, headers=headers).status_code, 401)

    def test_knowledge_access_and_mirror_guards_remain(self):
        for route in ("/api/knowledge/upload", "/api/wissen/upload"):
            with self.subTest(route=route):
                upload = {"files": {"files": ("test.md", b"test")}}
                self.assertEqual(self.client.post(route, **upload).status_code, 401)
                self.assertEqual(self.client.post(route, headers=self.headers("reader"),
                                 data={"groups": "other"}, **upload).status_code, 403)
                self.assertEqual(self.client.post(route, headers=self.headers("editor"),
                                 data={"folder": "data/mirror", "groups": "own"}, **upload).status_code, 403)
        self.blocked.add("editor")
        self.assertEqual(self.client.post("/api/wissen/upload", headers=self.headers("editor"),
                         files={"files": ("x.md", b"x")}).status_code, 403)
        self.assertEqual(len(list(self.folder.glob("*.md"))), 120)

    def test_license_routes_and_background_enforcement_are_gone(self):
        for name in ("license_status", "license_set", "license_clear", "license_check",
                     "startup_license", "_sync_gate", "_lic_grenze_profile"):
            self.assertNotIn(name, FUNCTIONS)
        for name in ("license.py", "license_enforce.py", "license_root.pub"):
            self.assertFalse((ROOT / "backend" / name).exists())
        for file in ("backend/main.py", "backend/knowledge_sync.py"):
            for n in ast.walk(ast.parse((ROOT / file).read_text(encoding="utf-8"))):
                if isinstance(n, ast.ImportFrom) and n.module == "backend":
                    self.assertFalse({a.name for a in n.names} & {"license", "license_enforce"})
        self.assertIn('"data/license.json"', (ROOT / "backend/sandbox.py").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
