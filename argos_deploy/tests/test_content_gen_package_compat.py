from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


DEPLOY_ROOT = Path(__file__).resolve().parents[1]
if str(DEPLOY_ROOT) not in sys.path:
    sys.path.insert(0, str(DEPLOY_ROOT))


class ContentGenPackageCompatibilityTests(unittest.TestCase):
    def test_package_exports_real_content_generator(self) -> None:
        for name in list(sys.modules):
            if name == "src.skills.content_gen" or name.startswith("src.skills.content_gen."):
                sys.modules.pop(name, None)

        try:
            module = importlib.import_module("src.skills.content_gen")
        except Exception as exc:
            self.fail(f"src.skills.content_gen must import cleanly: {exc!r}")

        self.assertTrue(hasattr(module, "ContentGen"), "ContentGen export is missing")
        runtime = module.ContentGen()
        self.assertTrue(callable(getattr(runtime, "generate_digest", None)))
        self.assertTrue(callable(getattr(runtime, "publish", None)))

        implementation = Path(sys.modules[module.ContentGen.__module__].__file__).resolve()
        self.assertEqual(implementation.name, "content_gen.py")
        self.assertEqual(implementation.parent.name, "skills")


if __name__ == "__main__":
    unittest.main()
