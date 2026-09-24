import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit


def install():
    output = Path(os.environ["ARGOS_TEST_OUTPUT_ROOT"]).resolve()

    def writable(path):
        resolved = Path(os.fsdecode(path)).resolve()
        return resolved == Path(os.devnull) or resolved.is_relative_to("/tmp") or resolved.is_relative_to(output)

    def audit(event, args):
        if event in {"socket.connect", "socket.sendto"} and isinstance(args[-1], tuple):
            if args[-1][0] not in {"127.0.0.1", "::1", "localhost"}:
                raise OSError("offline validation: external network prohibited")
        if event == "socket.getaddrinfo" and args[0] not in {"127.0.0.1", "::1", "localhost", None}:
            raise OSError("offline validation: external DNS prohibited")
        if event == "os.system":
            raise OSError("offline validation: shell launch prohibited")
        if event in {"subprocess.Popen", "os.posix_spawn"}:
            executable, command = args[:2]
            cwd = args[2] if event == "subprocess.Popen" else None
            executable = os.fsdecode(executable)
            allowed = False
            if isinstance(command, (list, tuple)) and Path(cwd or os.getcwd()).resolve().is_relative_to("/tmp"):
                name = Path(executable).name
                allowed = name in {"echo", "sleep"}
                if name == "git" and len(command) > 1:
                    allowed = command[1] in {"init", "add", "commit", "log", "checkout", "show", "diff", "rev-parse", "ls-tree"}
                    if command[1] == "config":
                        allowed = len(command) > 2 and command[2] in {"user.email", "user.name"}
                if executable == sys.executable:
                    allowed = list(command[1:3]) == ["-m", "unittest"]
            if not allowed:
                raise OSError("offline validation: unapproved subprocess prohibited")
        if event == "open":
            path, _, flags = args
            if isinstance(path, (str, bytes)) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                if not writable(path):
                    raise PermissionError("offline validation: writes require temporary state")
        if event == "sqlite3.connect" and args[0] != ":memory:":
            path = os.fsdecode(args[0])
            if path.startswith("file:"):
                uri = urlsplit(path)
                path = unquote(uri.path)
                if uri.netloc not in {"", "localhost"}:
                    raise PermissionError("offline validation: SQLite remote URI prohibited")
            if not writable(path):
                raise PermissionError("offline validation: SQLite requires temporary state")
        if event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.chmod", "os.chown", "os.link", "os.symlink"}:
            paths = args[:2] if event in {"os.rename", "os.link", "os.symlink"} else args[:1]
            if any(isinstance(path, (str, bytes)) and not writable(path) for path in paths):
                raise PermissionError("offline validation: mutations require temporary state")

    sys.addaudithook(audit)
