# ARGOS inference resilience and detailed health design

Date: 2026-08-16
Status: approved (Option A)

## Problem

The deployed Railway runtime is `argos_deploy`. Its current inference path calls
`_ensure_ollama_running()` at startup and before every Ollama request. When the
container has no `ollama` executable and localhost:11434 is closed, this creates
an unbounded retry/log loop. The public health API exposes only readiness,
uptime, and init error. Existing CI can import the root `src` duplicate instead
of the deployed `argos_deploy/src` package, and APK/Windows builds are not
sequenced behind runtime tests.

Railway currently has no inference-provider credential or inference endpoint
variable on `argos-full`. Therefore no cloud provider may be reported as
available until it is explicitly configured and successfully contacted.

## Design

### Inference

Keep provider policy in `ArgosCore` and add explicit runtime state:

- Ollama probes use a short timeout.
- A missing local executable permanently opens the Ollama circuit until process
  restart. Subsequent requests return immediately and do not spawn or log the
  same error.
- Reachability/start failures use a bounded cooldown configured by
  `ARGOS_OLLAMA_RETRY_SECONDS`.
- Remote `OLLAMA_HOST` values are probed but never locally auto-started.
- `ARGOS_OLLAMA_AUTOSTART=false` disables local process spawning.
- Add an optional OpenAI-compatible cloud provider configured only through
  `ARGOS_INFERENCE_URL`, `ARGOS_INFERENCE_MODEL`, and optional
  `ARGOS_INFERENCE_API_KEY`. It is included in Auto only when URL and model are
  present; credentials are never returned by health output.
- Auto mode tries configured cloud providers and the custom cloud fallback
  before Ollama. If none succeeds, it returns the existing offline response.

### Detailed health

Add `GET /health/details`. The response is generated from live runtime objects
and bounded collectors, with unavailable sources represented explicitly rather
than inferred:

- service readiness, UTC timestamp and uptime;
- P2P registry nodes (local plus live registry peers);
- MemPalace availability and constant-time drawer count;
- CPU, RAM, disks and GPU/VRAM/utilization from system-health collectors;
- inference provider configuration/circuit status, selected mode, last error,
  last check and next probe;
- container identity, Railway-presence flag, persistent-volume writability,
  and aggregate Docker state only when a Docker socket/CLI is available.

No tokens, credentials, environment values, prompts, peer secrets, stable
internal identifiers, hostnames, filesystem paths, raw initialization errors,
exact custom endpoint URLs, Docker names/images, or mutation operations are
exposed.

### CI

Make the existing APK and Windows workflows reusable through `workflow_call`.
The release-validation workflow runs deployed-runtime tests from
`argos_deploy`, then calls APK, then Windows with explicit `needs`. Pull
requests run tests only; a push to main performs the complete chain and uploads
both artifacts.

## Acceptance criteria

1. Repeated Ollama requests after a missing binary cause at most one spawn
   attempt per process and immediately return while the circuit is open.
2. A configured custom endpoint participates before Ollama; an unconfigured
   endpoint is not advertised as available.
3. `/health/details` always returns a stable JSON shape and marks missing
   telemetry with `available: false`.
4. Health output contains no configured secret values.
5. Runtime tests pass from the deployed directory.
6. On the final main HEAD, tests pass before APK, and APK passes before the
   Windows installer.
7. The produced APK is installed and launched on an ADB-visible Android target;
   launch UI and crash logs are inspected.
