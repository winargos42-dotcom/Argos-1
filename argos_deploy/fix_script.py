import os
content = open("src/connectivity/telegram_bot.py", "r", encoding="utf-8").read()
marker = """            sock.close()
        except Exception:
            pass

    async def cmd_tg_health"""
new_methods = '''
    # APK BUILD SUPPORT

    def _build_apk_sync(self) -> tuple[bool, str]:
        """Synchronous APK build."""
        import shlex
        cmd = os.getenv("ARGOS_APK_BUILD_CMD", "").strip()
        if not cmd:
            return False, "ARGOS_APK_BUILD_CMD is not set"
        if not cmd.strip():
            return False, "ARGOS_APK_BUILD_CMD is empty"
        try:
            args = shlex.split(cmd)
        except ValueError:
            return False, "ARGOS_APK_BUILD_CMD parse error"
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                return False, f"Build failed: {result.stderr[:200]}"
        except subprocess.CalledProcessError as e:
            return False, f"CalledProcessError: {e}"
        except Exception as e:
            return False, f"Build error: {e}"
        apk_path = self._find_apk_artifact()
        if not apk_path:
            return False, "APK file not found after build"
        return True, apk_path

    def _find_apk_artifact(self) -> str | None:
        """Search for APK file."""
        import glob
        patterns = ["build/bin/*.apk", "bin/*.apk", "*.apk", "build/*.apk", "app/build/outputs/apk/**/*.apk"]
        for pattern in patterns:
            matches = glob.glob(pattern, recursive=True)
            if matches:
                return sorted(matches, key=os.path.getmtime, reverse=True)[0]
        return None

'''
replacement = """            sock.close()
        except Exception:
            pass
""" + new_methods + """
    async def cmd_tg_health"""
content = content.replace(marker, replacement)
open("src/connectivity/telegram_bot.py", "w", encoding="utf-8").write(content)
print("OK")
