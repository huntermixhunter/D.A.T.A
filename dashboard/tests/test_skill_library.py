"""Skill Library, bundled-skill sync, and Mac desktop-control helpers.

Hermetic: every test points the skill folders, bundle and catalog at its own
temp directory, and GitHub is replaced by in-memory fakes. Nothing touches the
real ~/.claude/skills or the Hermes folder, and nothing goes on the network.
"""
import http.client
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_control  # noqa: E402
import install_skills  # noqa: E402
import self_update  # noqa: E402
import skill_library  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TempSkillDirs(unittest.TestCase):
    """Points install_skills and skill_library at throwaway folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.claude = root / "claude-skills"
        self.hermes = root / "hermes-skills"
        self.bundle = root / "bundle"
        self.patches = [
            mock.patch.object(install_skills, "CLAUDE_SKILLS_DIR", self.claude),
            mock.patch.object(install_skills, "HERMES_SKILLS_DIR", self.hermes),
            mock.patch.object(install_skills, "BUNDLE_DIR", self.bundle),
            mock.patch.object(install_skills, "MANIFEST", self.bundle / "manifest.json"),
            mock.patch.object(install_skills, "STATE_FILE", self.bundle / ".install_state.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class BundleSyncTest(TempSkillDirs):
    def setUp(self):
        super().setUp()
        write(self.bundle / "claude" / "alpha" / "SKILL.md", "alpha v1")
        write(self.bundle / "hermes" / "apple" / "notes" / "skill.md", "mac only")
        write(self.bundle / "manifest.json", json.dumps({
            "claude": ["alpha"],
            "hermes": [{"category": "apple", "name": "notes", "platforms": ["darwin"]}],
        }))

    def test_platform_filter(self):
        with mock.patch.object(install_skills.sys, "platform", "win32"):
            stats = install_skills.run("missing", quiet=True)
        self.assertEqual(stats["installed"], ["claude/alpha"])
        self.assertEqual(stats["other_platform"], 1)
        self.assertFalse((self.hermes / "apple" / "notes").exists())
        with mock.patch.object(install_skills.sys, "platform", "darwin"):
            stats = install_skills.run("sync", quiet=True)
        self.assertEqual(stats["installed"], ["hermes/apple/notes"])
        self.assertTrue((self.hermes / "apple" / "notes" / "skill.md").exists())

    def test_sync_refreshes_only_untouched_copies(self):
        install_skills.run("missing", quiet=True)
        write(self.bundle / "claude" / "alpha" / "SKILL.md", "alpha v2")
        stats = install_skills.run("sync", quiet=True)
        self.assertEqual(stats["refreshed"], ["claude/alpha"])
        self.assertEqual((self.claude / "alpha" / "SKILL.md").read_text(encoding="utf-8"), "alpha v2")

        write(self.claude / "alpha" / "SKILL.md", "my own edit")
        write(self.bundle / "claude" / "alpha" / "SKILL.md", "alpha v3")
        stats = install_skills.run("sync", quiet=True)
        self.assertEqual(stats["kept"], ["claude/alpha"])
        self.assertEqual((self.claude / "alpha" / "SKILL.md").read_text(encoding="utf-8"), "my own edit")

    def test_sync_does_not_resurrect_a_removed_skill(self):
        install_skills.run("missing", quiet=True)
        import shutil
        shutil.rmtree(self.claude / "alpha")
        stats = install_skills.run("sync", quiet=True)
        self.assertEqual(stats["kept"], ["claude/alpha"])
        self.assertFalse((self.claude / "alpha").exists())

    def test_existing_unrecorded_copy_is_left_alone(self):
        write(self.claude / "alpha" / "SKILL.md", "installed by an older DATA, then edited")
        stats = install_skills.run("sync", quiet=True)
        self.assertEqual(stats["kept"], ["claude/alpha"])

    def test_fingerprint_ignores_case_and_library_marker(self):
        a, b = self.root / "a", self.root / "b"
        write(a / "SKILL.md", "same")
        write(b / "skill.md", "same")
        write(b / ".data-library.json", "{}")
        self.assertEqual(install_skills.fingerprint(a), install_skills.fingerprint(b))


class LibraryBase(TempSkillDirs):
    def setUp(self):
        super().setUp()
        self.lib = self.root / "repo" / "skills_library"
        write(self.lib / "claude" / "plan" / "SKILL.md", "---\ndescription: plan\n---\nPlan.")
        write(self.lib / "hermes" / "mlops" / "vllm" / "SKILL.md", "vllm")
        write(self.lib / "hermes" / "mlops" / "vllm" / "references" / "serve.md", "serve")
        self.catalog = self.root / "catalog.json"
        write(self.catalog, json.dumps({"groups": {"build": "Plan and build"}, "skills": [
            {"id": "plan", "name": "plan", "kind": "claude", "category": "", "group": "build",
             "path": "skills_library/claude/plan"},
            {"id": "vllm", "name": "vllm", "kind": "hermes", "category": "mlops", "group": "build",
             "path": "skills_library/hermes/mlops/vllm"},
            {"id": "evil", "name": "evil", "kind": "hermes", "category": "..", "group": "build",
             "path": "skills_library/hermes/x/evil"},
        ]}))
        for p in (mock.patch.object(skill_library, "CATALOG_FILE", self.catalog),
                  mock.patch.object(skill_library, "INSTALL_ROOT", self.root / "repo"),
                  mock.patch.object(skill_library, "LOCAL_LIBRARY", self.lib),
                  mock.patch.object(skill_library, "BACKUP_DIR", self.root / "backups")):
            p.start()
            self.patches.append(p)
        skill_library._tree_cache.update(at=0.0, entries=None)

    def states(self):
        return {s["id"]: s["state"] for s in skill_library.listing()["skills"]}


class LibraryTest(LibraryBase):
    def test_local_install_and_remove(self):
        self.assertEqual(self.states()["plan"], "absent")
        skill_library.install("plan")
        skill_library.install("vllm")
        self.assertEqual(self.states()["plan"], "installed")
        self.assertTrue((self.claude / "plan" / "SKILL.md").exists())
        # Nested Hermes skills land two levels deep, where the bridge looks.
        self.assertTrue((self.hermes / "mlops" / "vllm" / "references" / "serve.md").exists())
        skill_library.remove("plan")
        self.assertFalse((self.claude / "plan").exists())
        self.assertEqual(self.states()["plan"], "absent")

    def test_uninstaller_finds_library_skills(self):
        import uninstall_skills
        skill_library.install("plan")
        skill_library.install("vllm")
        write(self.claude / "mine" / "SKILL.md", "user skill, no marker")
        with mock.patch.object(uninstall_skills, "CLAUDE_SKILLS_DIR", self.claude),              mock.patch.object(uninstall_skills, "HERMES_SKILLS_DIR", self.hermes):
            found = sorted(d.name for d in uninstall_skills._library_installed())
            self.assertEqual(found, ["plan", "vllm"])
            self.assertTrue(uninstall_skills._library_unmodified(self.claude / "plan"))
            write(self.claude / "plan" / "SKILL.md", "edited")
            self.assertFalse(uninstall_skills._library_unmodified(self.claude / "plan"))

    def test_never_touches_a_folder_it_did_not_install(self):
        write(self.claude / "plan" / "SKILL.md", "the user's own plan skill")
        self.assertEqual(self.states()["plan"], "present")
        with self.assertRaises(skill_library.SetupError):
            skill_library.install("plan")
        with self.assertRaises(skill_library.SetupError):
            skill_library.remove("plan")
        self.assertEqual((self.claude / "plan" / "SKILL.md").read_text(encoding="utf-8"),
                         "the user's own plan skill")

    def test_edited_copy_is_backed_up_on_remove(self):
        skill_library.install("plan")
        write(self.claude / "plan" / "SKILL.md", "edited")
        self.assertEqual(self.states()["plan"], "modified")
        result = skill_library.remove("plan")
        self.assertIn("saved to", result["message"])
        saved = list((self.root / "backups").rglob("SKILL.md"))
        self.assertEqual([p.read_text(encoding="utf-8") for p in saved], ["edited"])

    def test_rejects_unknown_and_unsafe_entries(self):
        for bad in ("nope", "../plan", "", None):
            with self.assertRaises(skill_library.SetupError):
                skill_library.install(bad)
        with self.assertRaises(skill_library.SetupError):
            skill_library.install("evil")

    def test_github_install_verifies_every_file(self):
        files = {"skills_library/claude/plan/SKILL.md": b"from github",
                 "skills_library/claude/plan/extra/notes.md": b"notes"}
        tree = [{"path": p, "sha": self_update._git_blob_sha(b), "size": len(b)} for p, b in files.items()]
        served = dict(files)

        def fake_download(entry):
            data = served[entry["path"]]
            if self_update._git_blob_sha(data) != entry["sha"]:
                raise RuntimeError("integrity check failed")
            return data

        with mock.patch.object(skill_library, "LOCAL_LIBRARY", self.root / "absent"), \
             mock.patch.object(skill_library, "_remote_tree", return_value=tree), \
             mock.patch.object(self_update, "_download_verified", side_effect=fake_download):
            skill_library.install("plan")
            self.assertEqual((self.claude / "plan" / "extra" / "notes.md").read_bytes(), b"notes")
            skill_library.remove("plan")
            served["skills_library/claude/plan/extra/notes.md"] = b"tampered"
            with self.assertRaises(skill_library.SetupError):
                skill_library.install("plan")
        self.assertFalse((self.claude / "plan").exists())
        self.assertEqual([p.name for p in self.claude.iterdir()], [])   # no staging left behind

    def test_github_rejects_path_traversal(self):
        tree = [{"path": "skills_library/claude/plan/../../escape.md", "sha": "x", "size": 1}]
        with mock.patch.object(skill_library, "LOCAL_LIBRARY", self.root / "absent"), \
             mock.patch.object(skill_library, "_remote_tree", return_value=tree):
            with self.assertRaises(skill_library.SetupError):
                skill_library.install("plan")
        self.assertFalse((self.root / "escape.md").exists())


class HttpTest(LibraryBase):
    """The routes behind a real loopback server, with the Settings gate."""

    def setUp(self):
        super().setUp()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if not skill_library.handle_http(self):
                    self.send_error(404)

            do_POST = do_GET

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", "X-Data-Request": "1"}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        payload = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, payload

    def test_routes(self):
        status, data = self.request("GET", "/upgrades/skill-library")
        self.assertEqual(status, 200)
        self.assertEqual({s["id"] for s in data["skills"]}, {"plan", "vllm", "evil"})
        self.assertIn("screen_control", data)
        status, data = self.request("POST", "/upgrades/skill-library/install", {"id": "plan"})
        self.assertEqual((status, data["state"]), (200, "installed"))
        status, data = self.request("POST", "/upgrades/skill-library/install", {"id": "missing"})
        self.assertEqual(status, 404)

    def test_cross_site_requests_are_refused(self):
        status, _ = self.request("POST", "/upgrades/skill-library/install", {"id": "plan"},
                                 headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/upgrades/skill-library", headers={"X-Data-Request": "0"})
        self.assertEqual(status, 403)
        self.assertFalse((self.claude / "plan").exists())


class DesktopKeysTest(unittest.TestCase):
    def test_mac_key_names(self):
        with mock.patch.object(desktop_control, "_IS_MAC", True):
            self.assertEqual([desktop_control._key_name(k) for k in ("win", "cmd", "alt", "ctrl")],
                             ["command", "command", "option", "ctrl"])

    def test_windows_key_names(self):
        with mock.patch.object(desktop_control, "_IS_MAC", False):
            self.assertEqual([desktop_control._key_name(k) for k in ("win", "cmd", "alt", "return")],
                             ["win", "ctrl", "alt", "enter"])

    def test_retina_screenshot_scaled_to_click_space(self):
        from PIL import Image
        fake = mock.Mock()
        fake.size.return_value = (1440, 900)
        with mock.patch.object(desktop_control, "_IS_MAC", True), \
             mock.patch.object(desktop_control, "_pg", return_value=fake):
            out = desktop_control.to_click_space(Image.new("RGB", (2880, 1800)))
        self.assertEqual(out.size, (1440, 900))
        with mock.patch.object(desktop_control, "_IS_MAC", False):
            same = desktop_control.to_click_space(Image.new("RGB", (2880, 1800)))
        self.assertEqual(same.size, (2880, 1800))


if __name__ == "__main__":
    unittest.main()
