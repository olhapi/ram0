# Upstream maintenance boundary

This package is an isolated adaptation of `integrations/mem0-plugin` at
`1112be3e5eb5b0eed4f3e337c11bdaf8dcae7a9f` (`chore(release): bump SDK, CLI, and plugin versions (#6800)`).

Adapted files are the host plugin/MCP manifests, persistent configuration CLI
and protected JSON loader, stdio-to-Streamable-HTTP transport, lifecycle hook
manifests and entrypoints, settings, REST clients, category onboarding,
automatic retrieval and durable capture, the `ram0-memory` skill,
documentation, and test harness. Persistent client state lives at
`~/.config/ram0/config.json`; `ram0 setup`, `ram0 config test`, and
`ram0 config set-key` are Ram0-only adaptation seams.
The implementation keeps the Ram0 translation seam inside `ram0_client.py` and
the OpenCode `Ram0Client`; it does not modify `integrations/mem0-plugin`.

Upstream identity helpers, the Mem0 Platform SDK dependency, hosted endpoints,
telemetry/analytics, and platform-only operations are intentionally not copied.
Ram0 owner identity remains derived from its Bearer API key. Lifecycle behavior
was reimplemented against each supported host contract so raw prompts,
transcripts, source dumps, and credentials do not cross the capture boundary.

To update from upstream:

1. Diff `integrations/mem0-plugin` against the commit recorded above.
2. Review upstream manifest, hook, skill, capture, and OpenCode changes against
   the adapted-file inventory above.
3. Port only relevant behavior through the two Ram0 client adapters; do not
   couple lifecycle callers to HTTP details or restore hosted dependencies.
4. Run `pytest -q integrations/ram0-plugin/tests`, the OpenCode package test,
   type-check, and build commands, `scripts/check-ram0-plugin-docs.sh`, and the
   isolated offline `make -C server e2e-ram0-plugin` acceptance test.
5. Confirm the real Codex isolated install/list smoke still passes, then update
   this recorded upstream commit after the adaptation review is complete.
