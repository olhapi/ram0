# Ram0 Plugin Persistent Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the Ram0 marketplace and make every supported agent load a protected, persistent Ram0 URL and API key configured through `ram0 setup`, with matching dashboard Help and public documentation.

**Architecture:** A focused Python configuration module owns the JSON schema, permissions, validation, atomic writes, and environment-over-file precedence. A small installed CLI manages that file; Python lifecycle hooks and a standard-library stdio-to-Streamable-HTTP MCP transport consume the same loader, while OpenCode implements the same contract from shared fixtures. Marketplace manifests launch the adapter instead of interpolating secrets, and the dashboard Help generates the permanent setup flow.

**Tech Stack:** Python 3 standard library, pytest, POSIX shell, TypeScript 5, Bun, Node test runner, Next.js 15, Codex/Claude/Cursor marketplace JSON, MCP JSON-RPC over stdio and Streamable HTTP.

## Global Constraints

- Store persistent client configuration only at `~/.config/ram0/config.json` with directory mode `0700` and file mode `0600` on POSIX.
- Reject symlink and non-regular config targets; use same-directory atomic replacement and never print or log the API key.
- Persist exactly `api_url` and `api_key`; normalize an absolute HTTP(S) URL without credentials, query, fragment, or trailing slash; reject a blank key.
- Non-empty `RAM0_API_URL` and `RAM0_API_KEY` override their individual file fields for CI and deliberate per-process use.
- Keep `http://localhost:8888` only as the missing-URL development default; never provide a default API key.
- Name the marketplace `ram0-plugins`, expose only plugin `ram0`, and use `ram0@ram0-plugins` in current instructions.
- Do not modify implementation files under `integrations/mem0-plugin`; preserve the Ram0 fork's upstream boundary.
- The MCP adapter is transport-only: it forwards JSON-RPC without implementing, renaming, caching, or changing the six server-owned tools.
- Never place the API key in command arguments, process listings, prompts, page content, memories, telemetry, status output, exceptions, or snapshots.
- Keep normal installation free of shell-export and `launchctl` persistence instructions; environment configuration may appear only in an explicitly labeled development/CI section.
- Do not add a core Python dependency, publish a new registry package, change server authentication, or add OAuth/keychain behavior.
- Use `pnpm` for dashboard work and Bun for `integrations/ram0-plugin/.opencode-plugin`.
- Any CI workflow change remains out of scope without separate explicit approval.

---

### Task 1: Protected Python Configuration and Installed CLI

**Files:**
- Create: `integrations/ram0-plugin/scripts/ram0_config.py`
- Create: `integrations/ram0-plugin/scripts/ram0_cli.py`
- Create: `integrations/ram0-plugin/scripts/install_cli.py`
- Create: `integrations/ram0-plugin/bin/ram0`
- Create: `integrations/ram0-plugin/tests/config_contract.json`
- Create: `integrations/ram0-plugin/tests/test_ram0_cli.py`
- Modify: `integrations/ram0-plugin/scripts/ram0_settings.py`
- Modify: `integrations/ram0-plugin/tests/test_ram0_settings.py`
- Modify: `integrations/ram0-plugin/tests/test_source_boundaries.py`

**Interfaces:**
- Produces: `CONFIG_RELATIVE_PATH = Path(".config/ram0/config.json")`.
- Produces: `Ram0Config(api_url: str, api_key: str | None)` with `display() -> dict[str, str | bool]` that never includes a key.
- Produces: `config_path(home: Path | None = None) -> Path`.
- Produces: `load_config(environment: Mapping[str, str] | None = None, *, home: Path | None = None, require_key: bool = False) -> Ram0Config`.
- Produces: `write_config(api_url: str, api_key: str, *, home: Path | None = None) -> Path` and `update_config(*, api_url: str | None = None, api_key: str | None = None, home: Path | None = None) -> Path`.
- Produces: `ram0_cli.main(argv: Sequence[str] | None = None, *, environment: Mapping[str, str] | None = None, home: Path | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int`.
- Produces: `install_cli.main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int`, copying the focused runtime modules under `~/.local/share/ram0/` and installing `~/.local/bin/ram0` with mode `0755`.
- Preserves: `ram0_settings.load_settings()` as the lifecycle-facing compatibility interface, now delegating URL/key resolution to `load_config()` and retaining boolean environment controls.

- [ ] **Step 1: Add failing config and CLI contract tests**

Add cases that assert:

```python
def test_write_config_is_private_atomic_and_loads_after_environment_is_cleared(tmp_path):
    path = write_config("https://brain-api.olhapi.com/", "one-time-key", home=tmp_path)
    assert path == tmp_path / ".config/ram0/config.json"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_config({}, home=tmp_path) == Ram0Config("https://brain-api.olhapi.com", "one-time-key")

def test_environment_overrides_individual_file_fields(tmp_path):
    write_config("https://file.example", "file-key", home=tmp_path)
    assert load_config({"RAM0_API_URL": "https://env.example"}, home=tmp_path).api_key == "file-key"
    assert load_config({"RAM0_API_KEY": "env-key"}, home=tmp_path).api_url == "https://file.example"

def test_setup_never_echoes_the_key(tmp_path):
    stdout, stderr = io.StringIO(), io.StringIO()
    assert main(["setup", "--url", "https://brain-api.olhapi.com"], home=tmp_path,
                stdin=io.StringIO("secret-value\n"), stdout=stdout, stderr=stderr) == 0
    assert "secret-value" not in stdout.getvalue() + stderr.getvalue()
    assert json.loads((tmp_path / ".config/ram0/config.json").read_text())["api_key"] == "secret-value"
```

Also cover invalid URL forms, blank keys, `config show` redaction, `config test`, `config set-url`, `config set-key`, cancelled prompts preserving old values, atomic replacement cleanup, symlink rejection, non-regular-file rejection, group/world-readable file rejection with `chmod 600` remediation, installer file modes, and a `PATH` warning.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_ram0_cli.py integrations/ram0-plugin/tests/test_ram0_settings.py integrations/ram0-plugin/tests/test_source_boundaries.py
```

Expected: collection/import failures for `ram0_config`, `ram0_cli`, and `install_cli`, plus old environment-only expectations.

- [ ] **Step 3: Implement the protected loader and atomic writer**

Implement `ram0_config.py` with standard-library-only primitives:

```python
@dataclass(frozen=True)
class Ram0Config:
    api_url: str
    api_key: str | None = field(repr=False)

def load_config(environment=None, *, home=None, require_key=False) -> Ram0Config:
    source = os.environ if environment is None else environment
    stored = _read_private_json(config_path(home))
    api_url = normalize_api_url(source.get("RAM0_API_URL") or stored.get("api_url") or DEFAULT_RAM0_API_URL)
    api_key = (source.get("RAM0_API_KEY") or stored.get("api_key") or "").strip() or None
    if require_key and api_key is None:
        raise Ram0ConfigError("Ram0 API key is missing; run `ram0 setup`.")
    return Ram0Config(api_url, api_key)
```

Use `lstat`, `stat.S_ISREG`, `os.open(..., O_CREAT | O_EXCL, 0o600)`, `json.dump`, `flush`, `os.fsync`, and `os.replace` in the destination directory. Re-check the destination immediately before replacement and clean up the temporary file in `finally`.

- [ ] **Step 4: Implement the CLI and bounded installer**

Implement hidden key entry with `getpass.getpass("Ram0 API key: ")` for a real terminal and injected `stdin` only for tests. `config show` must return only:

```text
Config: ~/.config/ram0/config.json
API URL: https://brain-api.olhapi.com
API key: configured (redacted)
```

`config test` loads with `require_key=True` and makes a bounded authenticated `GET <api_url>/categories` request; print status only. The installer initially copies only `ram0_cli.py`, `ram0_config.py`, and its launcher into explicit per-user paths, never edits shell profiles, and prints `~/.local/bin/ram0 setup` when the directory is not on `PATH`. Task 3 extends the same installer with the transport adapter once that file exists.

- [ ] **Step 5: Delegate lifecycle settings to the shared loader**

Replace URL/key parsing in `ram0_settings.load_settings()` with `load_config()`, add an injectable `home` keyword for tests, retain retrieval/capture boolean parsing, and translate config errors into display-safe `ValueError` messages.

- [ ] **Step 6: Run Task 1 tests**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_ram0_cli.py integrations/ram0-plugin/tests/test_ram0_settings.py integrations/ram0-plugin/tests/test_source_boundaries.py
```

Expected: all pass; no output or snapshot contains the test key.

- [ ] **Step 7: Commit Task 1**

```bash
git add integrations/ram0-plugin/bin/ram0 integrations/ram0-plugin/scripts/ram0_config.py integrations/ram0-plugin/scripts/ram0_cli.py integrations/ram0-plugin/scripts/install_cli.py integrations/ram0-plugin/scripts/ram0_settings.py integrations/ram0-plugin/tests/config_contract.json integrations/ram0-plugin/tests/test_ram0_cli.py integrations/ram0-plugin/tests/test_ram0_settings.py integrations/ram0-plugin/tests/test_source_boundaries.py
git commit -m "feat(plugin): add persistent Ram0 setup"
```

### Task 2: OpenCode Configuration Parity

**Files:**
- Create: `integrations/ram0-plugin/.opencode-plugin/ram0-config.ts`
- Create: `integrations/ram0-plugin/.opencode-plugin/ram0-config.test.ts`
- Modify: `integrations/ram0-plugin/.opencode-plugin/ram0-client.ts`
- Modify: `integrations/ram0-plugin/.opencode-plugin/opencode-ram0.ts`
- Modify: `integrations/ram0-plugin/.opencode-plugin/opencode-ram0.test.ts`

**Interfaces:**
- Consumes: `integrations/ram0-plugin/tests/config_contract.json` schema cases from Task 1.
- Produces: `loadRam0Config(environment: Environment, options?: {home?: string; readFile?: typeof readFile; stat?: typeof stat}): Promise<Ram0Config>`.
- Produces: `Ram0Config = {apiUrl: string; apiKey: string; retrievalEnabled: boolean; captureEnabled: boolean}`.
- Changes: `createRam0Hooks(options)` resolves configuration once through `loadRam0Config` before constructing `Ram0Client`.
- Changes: `Ram0Client` accepts `{apiUrl: string; apiKey: string}` instead of parsing environment variables itself.

- [ ] **Step 1: Add failing TypeScript contract tests**

Test file-only loading with an empty environment, individual environment overrides, localhost URL default, missing-key failure, unsafe POSIX mode rejection, malformed JSON, invalid URL cases from the shared fixture, and redacted errors:

```typescript
const config = await loadRam0Config({}, {home: fixtureHome});
expect(config.apiUrl).toBe("https://brain-api.olhapi.com");
expect(config.apiKey).toBe("stored-key");

const overridden = await loadRam0Config({RAM0_API_KEY: "env-key"}, {home: fixtureHome});
expect(overridden.apiUrl).toBe("https://brain-api.olhapi.com");
expect(overridden.apiKey).toBe("env-key");
```

- [ ] **Step 2: Run OpenCode tests and verify failure**

Run:

```bash
cd integrations/ram0-plugin/.opencode-plugin && bun test
```

Expected: module-not-found for `ram0-config.ts` and old environment-only assertions.

- [ ] **Step 3: Implement the config reader and refactor consumers**

Use `homedir()`, `join(home, ".config", "ram0", "config.json")`, `lstat`/`stat`, and `readFile`. Reject symlinks and POSIX modes with any `mode & 0o077`; normalize URL exactly as the shared fixtures require; preserve boolean environment controls. Pass the resolved object into both lifecycle REST calls and OpenCode's dynamic MCP registration so no generated config persists the key.

- [ ] **Step 4: Run OpenCode test, type-check, and build**

Run:

```bash
cd integrations/ram0-plugin/.opencode-plugin && bun test && bun run type-check && bun run build
```

Expected: all pass and `dist/index.js` plus declarations are verified.

- [ ] **Step 5: Commit Task 2**

```bash
git add integrations/ram0-plugin/.opencode-plugin/ram0-config.ts integrations/ram0-plugin/.opencode-plugin/ram0-config.test.ts integrations/ram0-plugin/.opencode-plugin/ram0-client.ts integrations/ram0-plugin/.opencode-plugin/opencode-ram0.ts integrations/ram0-plugin/.opencode-plugin/opencode-ram0.test.ts
git commit -m "feat(plugin): load persistent config in OpenCode"
```

### Task 3: Config-Aware MCP Transport Adapter

**Files:**
- Create: `integrations/ram0-plugin/scripts/mcp_stdio_adapter.py`
- Create: `integrations/ram0-plugin/tests/test_mcp_stdio_adapter.py`
- Modify: `integrations/ram0-plugin/.mcp.json`
- Modify: `integrations/ram0-plugin/.codex-mcp.json`
- Modify: `integrations/ram0-plugin/.cursor-mcp.json`
- Modify: `integrations/ram0-plugin/mcp_config.json`
- Modify: `integrations/ram0-plugin/scripts/install_cli.py`
- Modify: `integrations/ram0-plugin/tests/conftest.py`
- Modify: `integrations/ram0-plugin/tests/test_hooks.py`
- Modify: `integrations/ram0-plugin/tests/test_ram0_cli.py`

**Interfaces:**
- Consumes: `load_config(..., require_key=True)` from Task 1.
- Produces: `StreamableHttpTransport(endpoint: str, api_key: str, *, opener=urlopen, timeout=30)`.
- Produces: `send(message: dict[str, Any]) -> list[dict[str, Any]]`, `listen(emit: Callable[[dict[str, Any]], None], stop: Event) -> None`, and `close() -> None`.
- Produces: `run_stdio(stdin: TextIO, stdout: TextIO, stderr: TextIO, *, environment=None, home=None, transport_factory=StreamableHttpTransport) -> int`.
- Extends: `install_cli.main()` copies `mcp_stdio_adapter.py` into `~/.local/share/ram0/` beside `ram0_config.py`, allowing direct-MCP configurations to use one stable path outside versioned plugin caches.
- Manifest contract: Claude uses `${CLAUDE_PLUGIN_ROOT}/scripts/mcp_stdio_adapter.py`; Codex and Cursor use their supported plugin-root substitutions and the same script; no manifest contains either credential environment placeholder.

- [ ] **Step 1: Add failing transport and manifest tests**

Create a disposable HTTP fixture that supports JSON responses and SSE frames, returns `Mcp-Session-Id` on initialize, records `Authorization`, accepts `DELETE`, and can send a server notification. Assert:

```python
def test_stdio_adapter_forwards_initialize_and_keeps_session(tmp_path, streamable_mcp_server):
    write_config(streamable_mcp_server.base_url, "adapter-key", home=tmp_path)
    stdout = io.StringIO()
    code = run_stdio(io.StringIO(json.dumps(INITIALIZE) + "\n"), stdout, io.StringIO(), home=tmp_path)
    assert code == 0
    assert json.loads(stdout.getvalue())["id"] == INITIALIZE["id"]
    assert streamable_mcp_server.authorization_headers == ["Bearer adapter-key"]
```

Cover newline-delimited stdio, JSON HTTP response, SSE `event: message` parsing, multiple `data:` lines, `Mcp-Session-Id` propagation, `MCP-Protocol-Version`, client notifications with HTTP 202/no body, server notifications from GET, JSON-RPC errors unchanged, cancellation unchanged, DELETE on shutdown, network/auth/config diagnostics on stderr, invalid input isolation, output locking, and secret redaction.

Update manifest tests to require a stdio `command`/`args` entry and forbid `RAM0_API_URL`, `RAM0_API_KEY`, `Authorization`, and direct `url` fields. Extend installer tests to assert the stable `~/.local/share/ram0/mcp_stdio_adapter.py` copy exists with no group/world write bit.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_mcp_stdio_adapter.py integrations/ram0-plugin/tests/test_hooks.py
```

Expected: adapter import failure and manifest assertions failing against current direct HTTP definitions.

- [ ] **Step 3: Implement the standard-library adapter**

Read one JSON object per stdin line. For each message, POST compact JSON with `Content-Type: application/json` and `Accept: application/json, text/event-stream`; attach Bearer only to the configured endpoint; set the negotiated session and protocol headers after initialize. Parse either a JSON body or SSE blocks, emitting only JSON-RPC objects as single compact lines under a stdout lock. Run the server-notification GET loop only after a session ID exists. Treat HTTP 202/204 as no response, forward remote JSON-RPC errors unchanged, reject redirects through a no-redirect handler, and redact the key plus authorization values before writing diagnostics.

- [ ] **Step 4: Replace direct manifests and extend the CLI installer with the adapter**

Use each host's existing plugin-root variable and executable `python3`; pass the adapter path as an argument rather than using shell interpolation. Keep the server name `ram0`. Update `mcp_config.json` consistently or remove it only if tests prove no supported installer consumes it. Add `mcp_stdio_adapter.py` to the explicit install manifest in `install_cli.py` so Help can configure direct MCP against the stable per-user adapter path without copying a secret into any client config.

- [ ] **Step 5: Run adapter and manifest tests**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_mcp_stdio_adapter.py integrations/ram0-plugin/tests/test_hooks.py integrations/ram0-plugin/tests/test_ram0_cli.py
```

Expected: all pass with the disposable transport fixture and no credential strings in captured diagnostics.

- [ ] **Step 6: Commit Task 3**

```bash
git add integrations/ram0-plugin/scripts/mcp_stdio_adapter.py integrations/ram0-plugin/scripts/install_cli.py integrations/ram0-plugin/tests/test_mcp_stdio_adapter.py integrations/ram0-plugin/tests/conftest.py integrations/ram0-plugin/tests/test_hooks.py integrations/ram0-plugin/tests/test_ram0_cli.py integrations/ram0-plugin/.mcp.json integrations/ram0-plugin/.codex-mcp.json integrations/ram0-plugin/.cursor-mcp.json integrations/ram0-plugin/mcp_config.json
git commit -m "feat(plugin): bridge MCP through persistent config"
```

### Task 4: Ram0-Only Marketplace Identity and Migration Smoke

**Files:**
- Modify: `marketplace.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `.codex-plugin/marketplace.json`
- Modify: `.cursor-plugin/marketplace.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `integrations/ram0-plugin/tests/test_ram0_settings.py`
- Modify: `integrations/ram0-plugin/tests/test_hooks.py`

**Interfaces:**
- Marketplace contract: every root manifest has `name: "ram0-plugins"`, Ram0-owned display/owner metadata, and exactly one `plugins` entry whose name is `ram0` and whose local path is `./integrations/ram0-plugin`.
- Installer contract: Codex isolated smoke installs `ram0@ram0-plugins`; Claude validation, when available, validates the same catalog.

- [ ] **Step 1: Replace old assertions with failing Ram0-only assertions**

```python
for marketplace in MARKETPLACES:
    document = json.loads(marketplace.read_text())
    assert document["name"] == "ram0-plugins"
    assert [plugin["name"] for plugin in document["plugins"]] == ["ram0"]
    assert "Mem0 Plugins" not in marketplace.read_text()
```

Update the real Codex install expectation to `ram0@ram0-plugins` and add a source-boundary assertion that `integrations/mem0-plugin` still exists but is not catalogued by the Ram0 fork.

- [ ] **Step 2: Run marketplace tests and verify failure**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_ram0_settings.py integrations/ram0-plugin/tests/test_hooks.py
```

Expected: failures identify `mem0-plugins`, `Mem0 Plugins`, and the extra `mem0` entries.

- [ ] **Step 3: Rename and narrow all five manifests**

Use `Ram0 Plugins` for display metadata, `Ram0` for owner metadata, retain the existing Ram0 description/version/source/policy/category shapes per host, and delete only the Mem0 catalog entries—not the upstream source directory.

- [ ] **Step 4: Validate marketplace behavior**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_ram0_settings.py integrations/ram0-plugin/tests/test_hooks.py
claude plugin validate .
```

Expected: pytest passes; Claude validation passes when the installed CLI supports repository validation. Record an explicit skip if the CLI is unavailable, not a false success.

- [ ] **Step 5: Commit Task 4**

```bash
git add marketplace.json .claude-plugin/marketplace.json .codex-plugin/marketplace.json .cursor-plugin/marketplace.json .agents/plugins/marketplace.json integrations/ram0-plugin/tests/test_ram0_settings.py integrations/ram0-plugin/tests/test_hooks.py
git commit -m "fix(plugin): rename the Ram0 marketplace"
```

### Task 5: Lifecycle and Real-Stack File-Only Configuration

**Files:**
- Modify: `integrations/ram0-plugin/scripts/memory_capture.py`
- Modify: `integrations/ram0-plugin/scripts/setup_coding_categories.py`
- Modify: `integrations/ram0-plugin/tests/test_memory_capture.py`
- Modify: `integrations/ram0-plugin/tests/test_coding_categories.py`
- Modify: `tests/server/test_e2e_ram0_plugin.py`
- Modify: `server/scripts/e2e_ram0_plugin.py`

**Interfaces:**
- Consumes: `ram0_settings.load_settings(environment=None, *, home=None)` from Task 1.
- Changes: lifecycle entry functions accept an optional home/config root only for tests; production defaults remain the current user's home.
- Changes: E2E runner writes an account-specific protected config file and clears both Ram0 environment variables before invoking lifecycle and adapter code.

- [ ] **Step 1: Add failing file-only lifecycle tests**

Update tests to write config under a temporary home, pass an empty environment, and assert retrieval, capture, onboarding, owner fingerprints, key redaction, and fail-open missing-config output. Add a regression that the raw key never appears in plugin data filenames or saved state.

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_memory_capture.py integrations/ram0-plugin/tests/test_coding_categories.py
```

Expected: file-only cases fail while code still reads only `os.environ`.

- [ ] **Step 3: Thread the shared loader through lifecycle entry points**

Replace messages such as `set RAM0_API_KEY` and `check RAM0_API_URL` with the actionable, non-secret `run \`ram0 setup\`` or `run \`ram0 config test\`` wording. Preserve fail-open return values and existing capture/retrieval defaults.

- [ ] **Step 4: Extend the isolated real-stack acceptance**

Have the runner create `~/.config/ram0/config.json` with `0700/0600`, remove `RAM0_API_URL` and `RAM0_API_KEY` from the lifecycle process, exercise one MCP adapter `tools/list` call plus existing two-account REST lifecycle checks, and assert the six exact tool names and account isolation.

- [ ] **Step 5: Run lifecycle tests and the prepared E2E**

Run:

```bash
pytest -q integrations/ram0-plugin/tests/test_memory_capture.py integrations/ram0-plugin/tests/test_coding_categories.py
make -C server e2e-ram0-plugin
```

Expected: unit tests pass; the offline prepared stack proves file-only lifecycle and MCP transport against the real Ram0 server. If prepared images are absent or stale, report that exact prerequisite rather than substituting unit tests for live acceptance.

- [ ] **Step 6: Commit Task 5**

```bash
git add integrations/ram0-plugin/scripts/memory_capture.py integrations/ram0-plugin/scripts/setup_coding_categories.py integrations/ram0-plugin/tests/test_memory_capture.py integrations/ram0-plugin/tests/test_coding_categories.py tests/server/test_e2e_ram0_plugin.py server/scripts/e2e_ram0_plugin.py
git commit -m "test(plugin): verify persistent config end to end"
```

### Task 6: Dashboard Help Permanent Setup Flow

**Files:**
- Modify: `server/dashboard/src/app/(root)/dashboard/help/help-content.ts`
- Modify: `server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts`
- Modify: `server/dashboard/src/app/(root)/dashboard/help/page.tsx`

**Interfaces:**
- Changes: `AgentInstall` replaces `credentialSetup`, `credentialVerify`, and temporary remote-Bearer snippets with `cliInstall`, `persistentSetup`, `configVerify`, `directMcpSetup`, `pluginInstall`, `pluginNote`, `migration`, and `troubleshooting` fields.
- Produces: `persistentSetupCommand(apiUrl: string) -> string`, containing the reviewed-checkout CLI installation followed by `ram0 setup --url '<validated-url>'` and no secret.
- Preserves: `normalizedApiUrl`, `mcpUrl`, four client IDs/names, and `skillInstallCommand`.

- [ ] **Step 1: Rewrite Help tests first**

Require every client to expose a copyable setup flow containing:

```text
python3 ~/ram0-plugins/ram0/integrations/ram0-plugin/scripts/install_cli.py
ram0 setup --url 'https://api.example.test'
ram0 config test
```

Assert all current plugin commands use `ram0@ram0-plugins`, no generated content contains `export RAM0_API_URL`, `export RAM0_API_KEY`, `launchctl`, direct Bearer JSON, or a credential-shaped value, and all four tabs include correct restart/trust notes. Require visible migration, key rotation (`ram0 config set-key`), `chmod 600 ~/.config/ram0/config.json`, missing-config, and unreachable-endpoint guidance.

Require each `directMcpSetup` to register `python3 ~/.local/share/ram0/mcp_stdio_adapter.py` through that client's stdio MCP syntax. Claude Code and Codex use their CLI forms; Cursor uses command/args JSON; OpenCode uses its supported stdio config form. No direct setup may install the full plugin or duplicate a same-named remote MCP connection.

- [ ] **Step 2: Run Help tests and confirm failure**

Run:

```bash
node --experimental-strip-types --test 'server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts'
```

Expected: old environment/launchctl and `mem0-plugins` assertions fail.

- [ ] **Step 3: Implement the new Help content model**

Keep URL validation before interpolation. Generate secret-free setup and plugin commands; explain that the API Keys page shows the key once and `ram0 setup` reads it without echo into a `0600` file. Present direct MCP, full automation, and skills-only as mutually exclusive connection choices where applicable. Do not add an input field or browser storage for the API key.

- [ ] **Step 4: Update Help rendering and accessibility**

Replace “protected environment” and session-only copy with persistent-config language. Keep contextual copy-button labels and copied-state announcements. Add concise migration and troubleshooting sections without exposing copy buttons for any command that could contain a secret.

- [ ] **Step 5: Run Help tests, dashboard type-check, and formatting check**

Run:

```bash
node --experimental-strip-types --test 'server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts'
pnpm -C server/dashboard typecheck
pnpm -C server/dashboard exec prettier --check 'src/app/(root)/dashboard/help/page.tsx' 'src/app/(root)/dashboard/help/help-content.ts' 'src/app/(root)/dashboard/help/help-content.test.ts'
```

Expected: all pass.

- [ ] **Step 6: Commit Task 6**

```bash
git add 'server/dashboard/src/app/(root)/dashboard/help/help-content.ts' 'server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts' 'server/dashboard/src/app/(root)/dashboard/help/page.tsx'
git commit -m "fix(help): document persistent Ram0 setup"
```

### Task 7: Public Documentation and Contract Checks

**Files:**
- Modify: `integrations/ram0-plugin/README.md`
- Modify: `integrations/ram0-plugin/UPSTREAM.md`
- Modify: `docs/integrations/ram0-plugin.mdx`
- Modify: `docs/open-source/ram0-mcp.mdx`
- Modify: `server/README.md`
- Modify: `scripts/check-ram0-plugin-docs.sh`

**Interfaces:**
- Documentation contract: all four long-form surfaces name `~/.config/ram0/config.json`, `ram0 setup`, `ram0 config test`, `ram0 config set-key`, `ram0@ram0-plugins`, Bearer transport, environment override precedence, restart behavior, migration, direct/full/skills distinction, and secret exclusions.
- Negative contract: normal setup contains no `export RAM0_API_URL`, `export RAM0_API_KEY`, `launchctl`, `ram0@mem0-plugins`, literal placeholder key, or instruction to put the key in MCP JSON.

- [ ] **Step 1: Change the documentation checker first**

Update `require_in` assertions for the persistent path/commands and new marketplace name. Add negative `rg` checks scoped to normal installation sections, and retain the credential-shape, raw-content, telemetry, third-party, category-owner, and upstream-boundary checks. Continue running the Help Node test from the checker.

- [ ] **Step 2: Run the checker and verify failure**

Run:

```bash
bash scripts/check-ram0-plugin-docs.sh
```

Expected: failures list old environment-only setup and `ram0@mem0-plugins` across current docs.

- [ ] **Step 3: Rewrite all installation and security guidance consistently**

Document the reviewed checkout, CLI installation, hidden setup prompt, config modes, direct/full/skills choice, all client-specific install/restart/trust steps, old-marketplace migration, key rotation, unsafe-permission repair, missing/unreachable diagnostics, and explicitly labeled CI environment overrides. Update `UPSTREAM.md` to record the new config/transport files as Ram0-only adaptation seams.

- [ ] **Step 4: Run documentation coverage and formatting checks**

Run:

```bash
bash scripts/check-ram0-plugin-docs.sh
python scripts/check-llms-txt-coverage.py
```

Expected: both pass; no new `.mdx` page requires a `docs/llms.txt` entry because only existing pages changed.

- [ ] **Step 5: Commit Task 7**

```bash
git add integrations/ram0-plugin/README.md integrations/ram0-plugin/UPSTREAM.md docs/integrations/ram0-plugin.mdx docs/open-source/ram0-mcp.mdx server/README.md scripts/check-ram0-plugin-docs.sh
git commit -m "docs(plugin): publish persistent Ram0 setup"
```

### Task 8: Cross-Client Regression and Completion Verification

**Files:**
- Modify if failures expose missing coverage: only files already listed in Tasks 1-7

**Interfaces:**
- Verifies: configuration, CLI, lifecycle, transport, marketplace, OpenCode, dashboard Help, docs, and real-stack contracts as one release candidate.

- [ ] **Step 1: Run the complete Ram0 plugin Python suite**

Run:

```bash
pytest -q integrations/ram0-plugin/tests
```

Expected: all tests pass, including config permission, CLI redaction, transport, lifecycle, manifests, and source boundaries.

- [ ] **Step 2: Run OpenCode verification**

Run:

```bash
cd integrations/ram0-plugin/.opencode-plugin && bun test && bun run type-check && bun run build
```

Expected: all pass and exports exist.

- [ ] **Step 3: Run dashboard and documentation verification**

Run:

```bash
node --experimental-strip-types --test 'server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts'
pnpm -C server/dashboard typecheck
bash scripts/check-ram0-plugin-docs.sh
python scripts/check-llms-txt-coverage.py
```

Expected: all pass.

- [ ] **Step 4: Run marketplace and live acceptance checks**

Run:

```bash
claude plugin validate .
make -C server e2e-ram0-plugin
```

Expected: Claude accepts `ram0-plugins`; prepared offline E2E proves file-only setup, six MCP tools, lifecycle behavior, and two-account isolation. Report unavailable external CLIs or stale prepared images explicitly.

- [ ] **Step 5: Perform a clean-process manual smoke**

Using a temporary home and disposable test endpoint, install the CLI, run setup, start a new process with both `RAM0_API_URL` and `RAM0_API_KEY` unset, execute `ram0 config show` and one adapter initialize/tools-list exchange, and verify the output contains the URL/status but not the key.

- [ ] **Step 6: Inspect the final diff and repository state**

Run:

```bash
git diff --check
git status --short
git log --oneline -10
```

Expected: no whitespace errors; only intended changes remain; the pre-existing unrelated untracked plan files are still untouched.

- [ ] **Step 7: Commit only verification-driven fixes, if any**

If Step 1-6 required a code or test correction, stage only those explicit files and use the narrow Conventional Commit type matching the fix. If no files changed, do not create an empty commit.
