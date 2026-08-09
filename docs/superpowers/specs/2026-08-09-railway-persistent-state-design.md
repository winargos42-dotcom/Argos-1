# ARGOS Railway Persistent State Design

## Goal

Make `argos-full` preserve runtime memory, SQLite databases, node identity, encryption key, peer/DAG configuration, model state, and automatic backups across Railway redeploys without hiding or freezing application source code.

## Current state

The live `argos-full` service has no Railway volume. Mutable state is written under:

- `/app/data`: SQLite databases and WAL/SHM files, Chroma/vector data, model/trainer state, metrics, skill backups, and automatic backup ZIPs.
- `/app/config`: `node_id`, `master.key`, `node_birth`, `peers.json`, DAG definitions, and gateway configuration.
- `/app/logs`: currently empty and not required for recovery.

Application source is under `/app/src` and top-level Python files and must remain image-backed so Git deployments continue to update code normally.

## Approaches considered

### A. Mount volumes directly at `/app/data` and `/app/config`

This is structurally simple, but an empty first-time volume hides the data/config directories shipped in the image. It also requires two independent volume migrations and increases the chance of booting with missing seed files.

### B. Mount one volume at `/app/persist` and redirect state directories there — selected

Attach one Railway volume at `/app/persist`. Before ARGOS initializes, a small bootstrap routine ensures `/app/persist/data` and `/app/persist/config` exist, seeds them from the image only when necessary, then replaces `/app/data` and `/app/config` with symlinks to the persistent directories.

Benefits:

- one Railway volume and one mount;
- source code remains image-backed and updates normally;
- both SQLite state and node identity/key persist together;
- first boot can seed defaults safely;
- backup ZIPs under `/app/data/backups` persist automatically.

### C. Mount a volume over `/app`

Rejected because it would hide freshly deployed source files and make Git deployments unreliable.

## Runtime bootstrap

Add a focused module `argos_deploy/src/persistent_state.py` with a function `prepare_persistent_state(app_root: Path, state_root: Path) -> dict[str, str]`.

At startup, before importing `ArgosOrchestrator`:

1. Resolve `ARGOS_STATE_ROOT`, defaulting to `/app/persist`.
2. Create `${state_root}/data` and `${state_root}/config`.
3. For each state directory, if the persistent target is empty, copy the image-provided runtime directory into it.
4. Remove the image-backed runtime directory and replace it with a symlink to the persistent target.
5. Preserve file permissions for the `argos` runtime user.
6. Return the effective mappings for startup logging.

The routine must be idempotent: repeated starts must keep using existing persistent contents and must never overwrite a non-empty persistent directory with image defaults.

## Failure behavior

If `ARGOS_STATE_ROOT` cannot be created or is not writable, startup must report the persistence error and avoid silently claiming that durable state is active. The existing lightweight HTTP process may stay up for diagnostics, but ARGOS readiness must remain false until state preparation succeeds.

## Railway configuration

For service `argos-full` only:

- create/attach one Railway volume;
- mount it at `/app/persist`;
- set `ARGOS_STATE_ROOT=/app/persist`;
- keep the existing source, domain, port 8080, `python3 cloud_entry.py`, and `/health` configuration unchanged.

The existing `argos` VPN/API service must not be modified.

## Migration of current live state

Before final cutover, attempt an in-platform migration of the current running container's `/app/data` and `/app/config` into the new volume without exposing `master.key`. If Railway cannot copy the current ephemeral filesystem into a newly attached volume without replacing the instance, preserve at minimum the node identity/key through a secure Railway-side migration and document any unavoidable loss of the few minutes of freshly generated runtime DB state.

Secrets such as `master.key` must never be committed to GitHub or printed in user-visible logs.

## Verification

After deployment:

- Railway deployment status is `SUCCESS`.
- `/health` returns `ok=true`, `ready=true`, `error=null`.
- `/mcp` returns HTTP 200.
- `/app/data` and `/app/config` resolve to paths under `/app/persist`.
- `memory.db`, `argos_memory.db`, `life_support.db`, `node_id`, and `master.key` exist on the volume.
- trigger or observe an automatic backup in `/app/data/backups`.
- redeploy once and verify `node_id` is unchanged and previously written DB/backup files remain present.
