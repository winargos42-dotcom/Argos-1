# Cloud access and recovered configuration — 2026-09-18

## Findings

The user confirmed that working ARGOS credentials were already present in prior history. Personal Context located the historical `.env.bak`; the upstream file and this fork have the same blob, `45104b4a55dfb2285fe2a338884b300b889b857a`. The backup contains populated AI settings, including GigaChat client credentials. No secret values were printed or copied into this repair.

The prior providers report was only a configuration/port check. It overlooked the GigaChat client-ID/client-secret pair and access-token variable accepted by the core, so its zero count was insufficient evidence that credentials were unavailable.

The running cloud endpoint accepted MCP requests without an access check. Both cloud and main entrypoints also loaded dotenv with override enabled, allowing historical file values to replace deployed settings.

The deployment autouse test fixture eagerly loaded the entire core even for unrelated pure tests. Its fallback also suppressed exceptions and could leave direct class mutations untracked.

## Applied repository changes

- Added cloud-only bearer authentication through `ARGOS_MCP_API_KEY`, installed before runtime initialization. Public GET/HEAD status paths remain accessible. Missing access configuration fails closed; invalid or duplicate authorization is rejected before protected handler dispatch. WebSockets require authentication too.
- Kept CORS outside authentication so browser preflight succeeds without executing a protected handler. Actual requests still require the bearer token.
- Set both dotenv loaders to preserve values already supplied by the deployment environment.
- Recognized all supported GigaChat credential configurations; incomplete/placeholder values do not count. The report states that API operation and generated answers have not been verified. No models, prices or quotas were changed.
- Made the deployment test fixture wrap the lazy core loader and patch already cached classes without eagerly loading the core. Method patches are restored by pytest.
- Added explicit deployment working directories and import mode to validation. Root tests, helper/access tests, and selected runtime modules execute in separate processes. The release coverage threshold remains 30%.

## Verification

- Fixture regressions: baseline **3 failed, 1 passed**; after repair **4 passed**.
- GigaChat configuration regressions: baseline **9 failed, 6 passed**; after repair **15 passed**, using fake values and blocked network calls.
- Cloud access and environment precedence: **32 passed**, including the actual pre-initialization bootstrap and both real dotenv call sites. Baseline checks exposed unauthenticated dispatch and overwritten deployment values.
- Combined deployment helper/access/configuration command from `argos_deploy`: **78 passed**.
- Existing file-operation, self-healing, pricing and tool-calling tests with ordinary deployment conftest: **69 passed**.
- Root report/validation tests: **22 passed**. These totals cover **169 selected tests**, not the full application suite.
- Parent independently reviewed the delegated patches and corrected CORS ordering and the second dotenv override during review. Tests verify source selection against the deployment tree. Hosted Python 3.10 validation is required on the repair branch before applying this commit to main.
- The last main release run, `35334279832`, still failed 0% coverage versus 30%; no coverage threshold, skip list or failure reporting was weakened.

## Credential use and remaining rollout

Initially, automatic approval review rejected the attempted authenticated GigaChat and DeepSeek checks, citing a need for explicit user consent. The owner subsequently gave that consent on September 18. Authorized narrow provider checks then timed out; no successful provider authorization or inference was obtained. No recovered provider key was set in Railway.

A fresh `ARGOS_MCP_API_KEY` and `EXTERNAL_SEND_ENABLED=false`, `EXTERNAL_DRAFT_ONLY=true`, `EXTERNAL_REQUIRE_OWNER_APPROVAL=true` were staged on the existing `argos-full` service with `skipDeploys=true`. The provider rejected the deployment request, so the old runtime and persistent volume remain in place. The new access guard is not yet live. Once deployment is available, deploy the reviewed code while preserving the volume, verify rejected unauthenticated access, configure the intended provider, and verify a real authenticated AI response. Do not copy historical Telegram, device, outreach or infrastructure settings wholesale. Keep external communication restrictions enabled. Confirm the exact deployed commit rather than assuming a redeploy updates its source. Explicit credential consent is already recorded; it is no longer a pending question.

## Rollback

Revert this scoped code repair if necessary. No data migration or persistent-state rewrite occurred. Do not restore unauthenticated public MCP access on a service containing live provider credentials.

## Follow-up: Gist and P2P continuity

The cloud access repair was published as `d23e72eb87b11581e3c8740c90df87f5c385301f`. Branch run `35335503264` proved 784 source files and 169 selected tests on hosted Python 3.10; main validation `35335585344` succeeded. The live `argos-full` deployment remained `dcc86a80-63ed-4853-a530-5a65cf6a9243` during verification.

After the user recalled Gist/P2P, inspection found three distinct mechanisms: CI report publication to GitHub Gist, Gist command/telemetry files, and Grist tables (`ArgosStore`, `ArgosNodes`, `ArgosEvents`). Command Gist files overwrite their latest entry and are not a full MemPalace export. Grist can hold memory values, but the recovered backup contains placeholder Grist credentials with the integration disabled. No Gist target was identified from that backup or the two inspected public profile pages.

Canonical core initializers passed `core=self` to constructors requiring `gist_id` and `github_token`, then suppressed the resulting exception. Both source copies now initialize only with explicit ARGOS-prefixed Gist settings and matching constructor arguments. Absence, partial configuration or constructor failure leaves both clients disabled. Logs state that API access is unverified and listeners are not started. Eighteen regressions failed before repair and pass afterward, using actual constructors with fake credentials and blocked network/thread startup. Only the two initializer methods changed.

The previous dotenv fix exposed a strict string marker in `Dockerfile.legacy-recovery`. That build patch now accepts either the historical override=True call or the already corrected override=False call, while unexpected forms still abort. Tests execute the real heredoc over isolated temporary source copies, without booting ARGOS: baseline 1 failure/2 passes, repaired 3 passes plus 2 subtests. P2P, Telegram and Gist insertion blocks are preserved and their generated Python compiles. This is patch verification, not a full image build or deployment.

The Report to Gist workflow had never invoked its publisher; it only echoed a publication message. It now explicitly warns and records in the job summary that no Gist was updated. Its unused secret bindings were removed. Actual publication remains pending a verified target and report-generation wiring; no Gist was written here.

Remaining P2P gaps: config probes port 8000 but `connect_to(ip)` uses the bridge's default 55771/TCP; discovery uses 55772/UDP. The three stored Azure peers have not been probed or joined. Ordinary deployment startup leaves `core.p2p` unset, while the legacy Docker patch initializes it. Existing `/app/persist/config` contents are only seeded when empty. No configuration overwrite, peer connection, remote command or queue execution occurred.

Read-only checks of the older `argos-reboot-v2` endpoint obtained a status response (`ok=true`, AI mode Auto), while health/provider requests timed out. Recent legacy logs contained GigaChat HTTPS attempts and recurring Ollama failures; these do not prove a successful AI answer. No recovered key was used for these status/log reads.
