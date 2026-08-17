from validate_project import ProjectValidator


def test_validator_ignores_legacy_argos_deploy_python2_tree(tmp_path):
    """Legacy deployment archives are not part of the supported Python 3 runtime."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "good.py").write_text("value = 1\n", encoding="utf-8")

    legacy = tmp_path / "argos_deploy"
    legacy.mkdir()
    (legacy / "python2_only.py").write_text('print "legacy"\n', encoding="utf-8")

    validator = ProjectValidator(tmp_path)
    validator.check_python_syntax()

    assert validator.errors == []
