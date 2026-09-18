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

Automatic approval review rejected the attempted authenticated GigaChat and DeepSeek checks, citing a need for explicit user consent to transmit recovered credentials to those providers. No provider authorization or inference success was obtained. No recovered key was set in Railway, and no production deployment or volume was changed.

After explicit consent, configure the intended AI provider and a separate cloud-access bearer token in the existing `argos-full` service, deploy the reviewed code while preserving its volume, verify rejected unauthenticated access, and then verify a real authenticated AI response. Do not copy historical Telegram, device, outreach or infrastructure settings wholesale. Keep external communication restrictions enabled. Confirm the exact deployed commit rather than assuming a redeploy updates its source.

## Rollback

Revert this scoped code repair if necessary. No data migration or persistent-state rewrite occurred. Do not restore unauthenticated public MCP access on a service containing live provider credentials.
