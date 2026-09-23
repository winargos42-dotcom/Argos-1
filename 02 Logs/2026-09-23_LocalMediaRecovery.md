# Local media and P2P recovery — 2026-09-23

Preserved the verified local recovery in the canonical deployment source tree.
P2P uses authenticated, bounded LAN messages and accurate peer counts. Offline
voice uses Piper/Vosk with cancellable startup and bounded recorder cleanup.
Media inventory reports devices without starting capture. Vision and voice
cloud processing require explicit opt-in. Claude template stubs now report
execution_not_configured instead of falsely claiming a command ran.

Validation: all eleven focused test files passed separately in this worktree.
The 19 media/P2P files match the installed runtime and frozen source manifest.
Earlier live checks confirmed health, local P2P listener, camera frames,
microphone PCM, Piper playback success, synthetic Vosk recognition, clock and
read-only Home Assistant access. Real spoken wake-command accuracy is unverified.

Limitations: no external authenticated peer; LAN P2P payloads are not encrypted;
template stubs do not invoke Claude/Codex. The two truthful-template fixes are
preserved in source but not yet installed into the running process.
Railway rejected the pinned cloud deployment because its trial expired; GCP
management access is not authenticated. No cloud deployment or plan change was
performed by this source-preservation job. Full release CI/coverage is unverified.

Recovery: this is an isolated local branch; Claude's checkout and the running
service are unchanged. Revert this commit to undo the source transfer.
Next: independently inspect this branch, install the narrow template-status fix,
and resume cloud recovery after account access/billing is restored.
