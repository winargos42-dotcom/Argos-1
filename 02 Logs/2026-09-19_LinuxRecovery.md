# ARGOS Linux recovery — 2026-09-19

## Scope

Continued the owner's recovery request after reading the shared conversation and current project records. Base e87c0584165a7279b4a2f6bdc1c65405f1e00d65; isolated clean checkout /root/argos-recovery, branch codex/recovery-2026-09-19.

## Source repairs

- P2P probes use the actual bridge ARGOS_P2P_PORT/default 55771. Peer addresses, connection API and persistent peer files are unchanged.
- Explicit ARGOS_MEMPALACE_SQLITE_PATH selects read-only SQLite retrieval. Parameterized SQL, Unicode lexical matching, first 200 matches, first 4096 document characters, at most 10 results, 2-second/10-million-step budget. This is bounded lexical retrieval, not verified semantic similarity. Chroma remains the default when unset.
- Core receives at most 2400 characters of historical reference data. Stored instructions are not executable instructions; retrieval failures do not prevent an AI response.
- Platform identity reflects the runtime OS rather than hardcoded Windows. Validation includes three new regression files and the Redis dependency used by the real P2P bridge.

## Recovered artifacts (outside Git)

MemPalace: /root/argos-recovery-artifacts/mempalace_vec.sqlite3, 354754560 bytes, 92918 drawers, integrity_check=ok. SHA256 a50e87af84dec885f1712788222a71fd7c1d49354a7e591ec9f8f325bfc3e888; pinned HF revision 8b4bb12ef3024cc53758c8d2859ec017f6033f58. Original checksum remains unchanged after retrieval. Runtime has a separate copy under /root/argos-runtime/state/mempalace.

Chat dataset: /root/argos-recovery-artifacts/argos-chat-dataset/, 41805969 bytes across two parquet files. Both hashes match pinned revision cb6aa3d10602ba0c1da8222dfb22748e40722cf0. README reports 20010 rows; row counts were not independently computed. Artifact manifests record provenance. No records or credentials enter Git.

Drive recovery brief: https://docs.google.com/document/d/102G287Z3bI8QmvWjY1JKLBApedS1ajNBp1PwDxz2KQo/edit. Its historical Mistral/argos-dataset repository references are currently inaccessible; recovery is not claimed. HF Qwen v2 adapter contains real 161533192-byte weights; those weights were not downloaded.

## Local runtime

Arch Linux, Python 3.14, i5-3320M, 16 GB RAM; no supported discrete GPU identified. Dedicated environment /root/argos-recovery-venv, runtime /root/argos-runtime/app. Private /root/argos-runtime/argos.env is mode 0600 and untracked.

User services argos-local and argos-ollama are enabled; root linger=yes. Reboot itself untested. ARGOS binds 127.0.0.1:8080; Ollama binds 127.0.0.1:11434 with cloud disabled. Selected qwen2.5:1.5b returned 4 to a direct arithmetic smoke request. The preliminary 0.5B model failed and is not selected. This is not a model-quality benchmark.

ARGOS health: ok=true, ready=true, error=null. Authenticated MCP status: Ollama mode. Missing/invalid bearer keys: HTTP401. Recovered memory reports 92918 drawers and returns bounded lexical matches. External sending is disabled. Hardware, voice and Chroma integrations are not claimed configured.

End-to-end authenticated MCP command "Вычисли 2+2. Ответь только числом." returned "4" through the actual ARGOS core. Initial 120-second request timed out while the CPU processed its long prompt (~9–10 input tokens/second). Final model argos-local derives from qwen2.5:1.5b with context 2048, output cap 128, two CPU threads and temperature zero; OLLAMA_TIMEOUT=360. The local controller allows 420 seconds. First responses with substantial context can take several minutes. No extra weights were downloaded for this profile.

## Validation and review

- P2P: two meaningful failing registrations before repair, then passing regressions.
- SQLite: 8 failures before repair, 12 passes afterward.
- Core context: one failure before repair, three passing tests afterward.
- Full memory scan exceeded budget; bounded candidate query returned 5 results in 0.319 seconds, without printing excerpts.
- Final isolated cloud/helper suite: 184 passed, 2 subtests passed. Root tests: 22 passed. Existing isolated runtime module tests: 69 passed. Full release coverage remains unresolved; 30% gate unchanged.
- Initial root failure was pip absent from PATH; activating the dedicated environment fixed it. Sandbox TestClient hung; same isolated suite passed outside sandbox. Tests were not weakened.
- Independent railway_check review: no blockers for P2P, SQLite, core context, OS label or workflow additions. Test-generated identities were preserved outside checkout.

## External state

Railway argos-full still serves Aug10 commit 8a7799b77a2fbe5e17bdba42df23157e363643a7; logs show missing Ollama. Its attached 5GB volume remains intact. Normal redeploy repeats old code; exact newer SHA deployment is needed. Historical billing rejection was not independently retested. No bulk staged configuration was accepted.

HF embedding Space remains BUILD_ERROR from literal backslash-n requirements. Corrected requirements exist in GitHub; connector lacks repository-write scope. Gmail repeated authentication-required responses, so no mailbox findings were obtained.

Cloudflare MCP is configured at https://mcp.cloudflare.com/mcp; OAuth browser authorization pending. Registry precheck failed (fetch failed); no verdict claimed. No DNS or tunnel changes.

## Rollback and handoff

Revert source patch to roll back. Stop local services with systemctl --user stop argos-local argos-ollama. Backups remain separate from runtime state; no original-data overwrite or schema migration.

Next / upcoming task: publish reviewed branch for CI, complete Cloudflare/HF account authorization before external deployment. Full project/cloud recovery is not complete.
