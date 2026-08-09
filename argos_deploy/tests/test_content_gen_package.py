import unittest

from src.skills.content_gen import ContentGen


class ContentGenPackageTests(unittest.TestCase):
    def test_package_exports_runtime_class(self):
        skill = ContentGen()

        self.assertTrue(callable(skill.generate_digest))
        self.assertTrue(callable(skill.publish))


if __name__ == "__main__":
    unittest.main()
