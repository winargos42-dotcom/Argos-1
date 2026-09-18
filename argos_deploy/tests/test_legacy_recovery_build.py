import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "argos_deploy"
SOURCE_FILES = (
    "cloud_entry.py",
    "src/connectivity/telegram_bot.py",
    "src/argos_c2_gist.py",
)
DOTENV_CALL = "load_dotenv(env_path, override=False)"


class TestLegacyRecoveryBuild(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app_root = Path(temporary.name)
        for relative_path in SOURCE_FILES:
            target = self.app_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SOURCE_ROOT / relative_path).read_bytes())

    def _set_dotenv_call(self, replacement):
        path = self.app_root / "cloud_entry.py"
        source = path.read_text(encoding="utf-8")
        self.assertEqual(source.count(DOTENV_CALL), 1)
        path.write_text(source.replace(DOTENV_CALL, replacement, 1), encoding="utf-8")

    def _run_build_patch(self):
        dockerfile = PROJECT_ROOT / "Dockerfile.legacy-recovery"
        source = dockerfile.read_text(encoding="utf-8")
        patch = source.split("RUN python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
        tree = ast.parse(patch)
        remapped = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Path"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("/app/")
            ):
                relative_path = node.args[0].value.removeprefix("/app/")
                self.assertIn(relative_path, SOURCE_FILES)
                node.args[0].value = str(self.app_root / relative_path)
                remapped.append(relative_path)
        self.assertCountEqual(remapped, SOURCE_FILES)
        return subprocess.run(
            [sys.executable, "-I", "-c", ast.unparse(tree)],
            cwd=self.app_root,
            env={},
            capture_output=True,
            text=True,
            timeout=10,
        )

    def _assert_successful_patch(self):
        result = self._run_build_patch()
        self.assertEqual(result.returncode, 0, result.stderr)
        trees = {}
        for relative_path in SOURCE_FILES:
            source = (self.app_root / relative_path).read_text(encoding="utf-8")
            compile(source, relative_path, "exec")
            trees[relative_path] = ast.parse(source)

        cloud_calls = [
            node for node in ast.walk(trees["cloud_entry.py"])
            if isinstance(node, ast.Call)
        ]
        dotenv_calls = [
            node for node in cloud_calls
            if isinstance(node.func, ast.Name) and node.func.id == "load_dotenv"
        ]
        self.assertEqual(len(dotenv_calls), 1)
        override = next(kw.value for kw in dotenv_calls[0].keywords if kw.arg == "override")
        self.assertIs(ast.literal_eval(override), False)
        self.assertTrue(any(ast.unparse(node.func) == "core.p2p.start" for node in cloud_calls))

        polling_calls = [
            node for node in ast.walk(trees["src/connectivity/telegram_bot.py"])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_polling"
        ]
        self.assertTrue(any(
            kw.arg == "stop_signals" and ast.literal_eval(kw.value) is None
            for call in polling_calls for kw in call.keywords
        ))
        gist_classes = [
            node for node in trees["src/argos_c2_gist.py"].body
            if isinstance(node, ast.ClassDef)
            and node.name in {"GistC2", "GhostDroneClient"}
        ]
        self.assertEqual(len(gist_classes), 2)
        for cls in gist_classes:
            initializer = next(
                node for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            )
            self.assertEqual(initializer.args.args[-1].arg, "core")
            self.assertIs(ast.literal_eval(initializer.args.defaults[-1]), None)

    def test_legacy_dotenv_override_is_disabled(self):
        self._set_dotenv_call("load_dotenv(env_path, override=True)")
        self._assert_successful_patch()

    def test_current_sources_build_with_environment_override_disabled(self):
        self._assert_successful_patch()

    def test_unexpected_dotenv_markers_abort_before_modifying_sources(self):
        for call in ("load_dotenv(env_path, override=None)", "load_dotenv(env_path)"):
            with self.subTest(call=call):
                cloud_path = self.app_root / "cloud_entry.py"
                cloud_path.write_bytes((SOURCE_ROOT / "cloud_entry.py").read_bytes())
                self._set_dotenv_call(call)
                before = {path: (self.app_root / path).read_bytes() for path in SOURCE_FILES}
                result = self._run_build_patch()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cloud_entry dotenv override marker not found", result.stderr)
                self.assertEqual(
                    {path: (self.app_root / path).read_bytes() for path in SOURCE_FILES},
                    before,
                )


if __name__ == "__main__":
    unittest.main()
