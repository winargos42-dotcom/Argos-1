from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "argos_deploy"
HELPERS = ['tests/test_browser_conduit.py', 'tests/test_requirements_runtime_deps.py', 'tests/test_runtime_test_isolation.py', 'tests/test_cloud_auth.py', 'tests/test_ai_provider_configuration.py', 'tests/test_legacy_recovery_build.py', 'tests/test_gist_initialization.py', 'tests/test_gemini_compatibility.py', 'tests/test_huggingface_model_configuration.py', 'tests/test_peer_autoconnect_port.py', 'tests/test_mempalace_sqlite_recovery.py', 'tests/test_core_recovered_memory_context.py', 'tests/test_ollama_prompt_context.py', 'tests/test_safe_arithmetic.py', 'tests/test_ollama_input_policy.py', 'tests/test_core_direct_results.py', 'tests/test_direct_file_commands.py', 'tests/test_core_ollama_input_guard.py', 'tests/test_execution_reporting.py']
RUNTIME = ['tests/test_file_operations.py', 'tests/test_self_healing.py', 'tests/test_pricing.py', 'tests/test_tool_calling.py']
HELPERS += ["tests/test_golden_application.py", "tests/test_task_runtime.py",
            "tests/test_task_runtime_edges.py", "tests/test_task_control.py",
            "tests/test_control_api.py", "tests/test_control_integration.py", "tests/test_memory_index.py"]


def include_all_source(data_file):
    import coverage

    measured = coverage.Coverage(data_file=str(data_file))
    measured.load()
    measured.get_data().add_lines({str(path.resolve()): [] for path in (APP / "src").rglob("*.py")})
    measured.save()


def source_hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((APP / "src").rglob("*.py"))}


def test_command(tests, *, helpers=False):
    return [sys.executable, "-m", "pytest", "--rootdir=.", "-c", "../pytest.ini",
            "--import-mode=importlib", *(["--noconftest"] if helpers else []),
            *tests, "-q", "-o", "addopts=", "--cov=" + str(APP / "src"),
            "--cov-append", "--cov-report="]


def run_broad_tests(output, env):
    selected = {Path(path).name for path in HELPERS + RUNTIME}
    results = []
    guard = output / "guard"
    guard.mkdir(exist_ok=True)
    (guard / "sitecustomize.py").write_text("from deployed_validation_guard import install\ninstall()\n", encoding="utf-8")
    for test in sorted((APP / "tests").glob("test_*.py")):
        if test.name in selected:
            continue
        with tempfile.TemporaryDirectory(prefix="argos-app-validation-") as temporary:
            state = Path(temporary)
            for folder in ("src", "config"):
                if (APP / folder).exists():
                    shutil.copytree(APP / folder, state / folder, ignore=shutil.ignore_patterns("__pycache__"))
            for source in APP.glob("*.py"):
                shutil.copy2(source, state / source.name)
            isolated_env = {key: env[key] for key in ("PATH", "LANG", "LC_ALL", "COVERAGE_FILE") if key in env}
            isolated_env.update(
                PYTHONPATH=os.pathsep.join((str(guard), str(ROOT / "scripts"), str(APP))),
                PYTHONDONTWRITEBYTECODE="1", ARGOS_TEST_OUTPUT_ROOT=str(output),
                EXTERNAL_SEND_ENABLED="false", ARGOS_VECTOR_FORCE_FALLBACK="1", ARGOS_MEMPALACE="0",
                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
            )
            command = test_command([str(test)])
            command[command.index("--rootdir=.")] = "--rootdir=" + str(APP)
            command[command.index("../pytest.ini")] = str(ROOT / "pytest.ini")
            log_path = output / (test.stem + ".log")
            with log_path.open("w", encoding="utf-8") as log:
                try:
                    code = subprocess.run(command, cwd=state, env=isolated_env, stdout=log, stderr=subprocess.STDOUT, timeout=40).returncode
                except subprocess.TimeoutExpired:
                    code = 124
            results.append({"test": test.name, "exit_code": code})
            print(f"{test.name}: exit {code}", flush=True)
    (output / "broad-results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return [result["test"] for result in results if result["exit_code"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure the entire deployed src tree; enforce 30% statement coverage.")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "app-coverage")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = source_hashes()
    (output / "source-sha256.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ, COVERAGE_FILE=str(output / ".coverage"))
    subprocess.run([sys.executable, "-m", "coverage", "erase"], cwd=APP, env=env, check=True)
    failures = []
    for name, tests, helpers in [("helpers", HELPERS, True), ("runtime", RUNTIME, False)]:
        try:
            result = subprocess.run(test_command(tests, helpers=helpers), cwd=APP, env=env, timeout=180)
            if result.returncode:
                failures.append(name)
        except subprocess.TimeoutExpired:
            failures.append(name + " (timeout)")
    failures.extend(run_broad_tests(output, env))
    if source_hashes() != sources:
        failures.append("application source changed during validation")
    include_all_source(output / ".coverage")
    reports = [
        ["json", "-o", str(output / "coverage.json")],
        ["xml", "-o", str(output / "coverage.xml")],
        ["report", "--precision=2", "--fail-under=30"],
    ]
    for report in reports:
        result = subprocess.run([sys.executable, "-m", "coverage", *report], cwd=APP, env=env)
        if result.returncode:
            failures.append("coverage " + report[0])
    if failures:
        print("Validation failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
