import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


class ReleaseError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    try:
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        raise ReleaseError("Git validation failed") from exc


def allowed_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name or "\\" in name:
        raise ReleaseError("Invalid release path")
    parts = path.parts
    valid = name in ("argos_deploy/cloud_entry.py", "argos_deploy/main.py")
    valid |= len(parts) >= 3 and parts[:2] == ("argos_deploy", "src") and path.suffix == ".py"
    valid |= len(parts) == 4 and parts[:3] == ("argos_deploy", "src", "interface") and path.suffix == ".html"
    if not valid:
        raise ReleaseError("Path outside release allowlist")
    return Path(*parts[1:])


def safe_path(root, relative):
    if root.resolve() != root or root.is_symlink():
        raise ReleaseError("Release root must be canonical")
    path = root
    for part in Path(relative).parts:
        if part in ("..", "") or Path(part).is_absolute():
            raise ReleaseError("Invalid relative path")
        path = path / part
        if path.is_symlink():
            raise ReleaseError("Symlink in release path")
    return path


def current_hash(path):
    if not path.exists():
        return None
    if not path.is_file():
        raise ReleaseError("Expected regular file")
    return digest(path.read_bytes())


def blob(repo, revision, name):
    entry = git(repo, "ls-tree", revision, "--", name)
    if not entry:
        return None, None
    mode, kind, oid = entry.split(b"\t", 1)[0].decode().split()
    if mode not in ("100644", "100755") or kind != "blob":
        raise ReleaseError("Only regular source files can be released")
    return oid, git(repo, "cat-file", "blob", oid)


def build_plan(repo, runtime, base_ref):
    repo, runtime = Path(repo).resolve(), Path(runtime).resolve()
    if not runtime.is_dir():
        raise ReleaseError("Runtime directory is missing")
    base = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}").decode().strip()
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    files = []
    names = git(repo, "diff", "--name-only", "--no-renames", "-z", base, head).split(b"\0")
    for raw in names:
        if not raw:
            continue
        name = raw.decode()
        if not (name.startswith("argos_deploy/src/") or name in (
            "argos_deploy/src", "argos_deploy/cloud_entry.py", "argos_deploy/main.py"
        )):
            continue
        relative = allowed_path(name)
        old_blob, old = blob(repo, base, name)
        new_blob, new = blob(repo, head, name)
        if new is None:
            raise ReleaseError("Deleted files cannot be released")
        target = safe_path(runtime, relative)
        old_hash = digest(old) if old is not None else None
        if current_hash(target) != old_hash:
            raise ReleaseError("Runtime baseline differs from base commit")
        files.append({"path": name, "runtime_path": str(target), "old_blob": old_blob,
                      "new_blob": new_blob, "old_sha256": old_hash, "new_sha256": digest(new)})
    if not files:
        raise ReleaseError("No eligible changed files")
    return {"version": 1, "repo": str(repo), "runtime": str(runtime), "base": base, "head": head,
            "base_tree": git(repo, "rev-parse", f"{base}^{{tree}}").decode().strip(),
            "head_tree": git(repo, "rev-parse", f"{head}^{{tree}}").decode().strip(), "files": files}


def atomic_write(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".argos-release-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, data):
    atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode(), 0o600)


def plan(repo, runtime, base_ref, output):
    manifest = build_plan(repo, runtime, base_ref)
    if Path(output).resolve().is_relative_to(Path(manifest["runtime"])):
        raise ReleaseError("Manifest must be outside runtime")
    save(output, manifest)
    return manifest


def apply(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if manifest_path.is_relative_to(Path(manifest["runtime"]).resolve()):
        raise ReleaseError("Manifest must be outside runtime")
    if build_plan(manifest["repo"], manifest["runtime"], manifest["base"]) != manifest:
        raise ReleaseError("Manifest/source commit drift")
    sources = []
    for item in manifest["files"]:
        source = safe_path(Path(manifest["repo"]), item["path"])
        data = source.read_bytes()
        if digest(data) != item["new_sha256"]:
            raise ReleaseError("Source working file drift")
        sources.append(data)
    receipt_path = manifest_path.with_suffix(".receipt.json")
    if receipt_path.exists():
        raise ReleaseError("Receipt already exists")
    backups = Path(tempfile.mkdtemp(prefix="argos-backup-", dir=manifest_path.parent))
    receipt = {"version": 1, "runtime": manifest["runtime"], "head": manifest["head"], "files": []}
    for index, item in enumerate(manifest["files"]):
        target = Path(item["runtime_path"])
        record = dict(item, backup=None, mode=0o644)
        if item["old_sha256"] is not None:
            record["mode"] = target.stat().st_mode & 0o777
            backup = backups / f"{index}.bak"
            old_data = target.read_bytes()
            if digest(old_data) != item["old_sha256"]:
                raise ReleaseError("Runtime changed before backup")
            atomic_write(backup, old_data, 0o600)
            record["backup"] = str(backup)
        receipt["files"].append(record)
    installed = []
    try:
        for record, data in zip(receipt["files"], sources):
            target = safe_path(Path(receipt["runtime"]), allowed_path(record["path"]))
            if current_hash(target) != record["old_sha256"]:
                raise ReleaseError("Runtime changed during apply")
            atomic_write(target, data, record["mode"])
            installed.append(record)
        save(receipt_path, receipt)
    except BaseException:
        restore(dict(receipt, files=installed))
        raise
    return receipt_path


def restore(receipt):
    prepared = []
    seen = set()
    for item in receipt["files"]:
        target = safe_path(Path(receipt["runtime"]), allowed_path(item["path"]))
        if target in seen or str(target) != item["runtime_path"]:
            raise ReleaseError("Invalid receipt path")
        seen.add(target)
        if current_hash(target) != item["new_sha256"]:
            raise ReleaseError("Runtime drift prevents rollback")
        data = None
        if item["old_sha256"] is not None:
            backup = Path(item["backup"])
            if backup.is_symlink() or backup.resolve() != backup:
                raise ReleaseError("Symlink backup")
            data = backup.read_bytes()
            if digest(data) != item["old_sha256"]:
                raise ReleaseError("Backup checksum mismatch")
        prepared.append((item, data))
    for item, data in prepared:
        target = safe_path(Path(receipt["runtime"]), allowed_path(item["path"]))
        if str(target) != item["runtime_path"]:
            raise ReleaseError("Invalid receipt path")
        if current_hash(target) != item["new_sha256"]:
            raise ReleaseError("Runtime drift prevents rollback")
        if data is None:
            target.unlink()
        else:
            atomic_write(target, data, item["mode"])


def rollback(receipt_path):
    restore(json.loads(Path(receipt_path).read_text()))


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("plan")
    for name in ("repo", "runtime", "base-ref", "output"):
        planning.add_argument(f"--{name}", required=True)
    commands.add_parser("apply").add_argument("--manifest", required=True)
    commands.add_parser("rollback").add_argument("--receipt", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(args.repo, args.runtime, args.base_ref, args.output)
        print(args.output)
    elif args.command == "apply":
        print(apply(args.manifest))
    else:
        rollback(args.receipt)
        print("Rollback completed")


if __name__ == "__main__":
    main()
