# Ram0 Repository Rebase Design

Date: 2026-09-12

## Objective

Make `https://github.com/olhapi/ram0` the canonical Ram0 repository based on
`supermemoryai/supermemory`, while preserving the former Mem0-based repository
only in a verified local backup. The public repository must present Ram0 as an
unofficial Supermemory fork, retain required licenses and attribution, and
avoid publicly redistributing the ambiguously licensed local engine binary.

## Current state

- GitHub records `olhapi/ram0` as a public fork of `mem0ai/mem0`.
- The repository has no issues, pull requests, releases, stars, or downstream
  forks recorded on the fork itself.
- `feat/ram0-supermemory-cutover` is based directly on the current
  `supermemoryai/supermemory` `main` commit and is 16 commits ahead, 0 behind.
- The Supermemory-based branch contains the deployed gateway, MCP service,
  graph application, migration tooling, coding-agent integrations, and
  deployment runbook.
- The root Supermemory source is MIT licensed. `skills/supermemory` is a nested
  Apache-2.0 component. The prebuilt `supermemory-server` release asset does
  not state sufficiently explicit redistribution terms.

## Chosen approach

Replace the GitHub repository object after creating and validating a complete
local backup. Recreate the same `olhapi/ram0` name as a real GitHub fork of
`supermemoryai/supermemory`, then fast-forward its `main` branch with the Ram0
commit series.

Keeping the existing repository object would leave GitHub permanently showing
Ram0 as a Mem0 fork. Waiting for GitHub Support to detach and reattach the fork
would be slower and would not give a predictable completion path.

## Backup and rollback

Before any deletion:

1. Commit and push the documentation and compliance changes to the existing
   Supermemory cutover branch.
2. Create a timestamped bare mirror outside the working checkout containing
   every Git ref from the existing `olhapi/ram0` remote.
3. Create a portable Git bundle from the mirror.
4. Export non-secret repository metadata, branch names, tags, release metadata,
   issue metadata, pull-request metadata, rulesets, branch protections,
   Actions-variable names, environment names, webhook metadata without secret
   values, and deploy-key public metadata where accessible.
5. Record the remote default-branch commit and the cutover-branch commit.
6. Run `git fsck --full` on the mirror, verify the bundle, and prove both
   recorded commits are retrievable from the backup.

If recreation or publication fails, recreate `olhapi/ram0` as an independent
repository from the mirror and restore the original default branch. The local
backup is retained after successful migration; it is not deleted as cleanup.

## Repository replacement

After the backup passes:

1. Delete the existing GitHub repository.
2. Fork `supermemoryai/supermemory` into the same `olhapi/ram0` name.
3. Verify GitHub reports `supermemoryai/supermemory` as the parent.
4. Push the prepared Ram0 branch to `main` as a fast-forward from the upstream
   base, without importing Mem0 branches or Mem0 tags.
5. Set `main` as the default branch and update the local checkout so `origin`
   tracks the recreated repository while `upstream` remains the official
   Supermemory repository.
6. Confirm the public branch is 0 commits behind upstream at publication time
   and contains only the reviewed Ram0 delta above that base.

## Public identity and documentation

Replace the upstream root README with a concise Ram0 README that covers:

- Ram0's identity as an unofficial, self-hosted fork derived from Supermemory;
- the local engine, authenticated gateway/MCP, graph UI, agent hooks, and
  Mem0-to-Supermemory migration tooling;
- deployment and workstation installation links;
- the deployed ports and public endpoint pattern without credentials;
- the upstream synchronization policy;
- security boundaries and backup expectations; and
- license, attribution, nested-license, and engine-binary caveats.

Set the GitHub description to:

> Ram0 — an unofficial self-hosted Supermemory fork with MCP, graph UI, coding-agent integrations, and Mem0 migration tooling.

Use only Ram0 identity in the repository description and README title. Mention
Supermemory only for factual provenance and compatibility. Do not use upstream
logos as Ram0 branding or imply endorsement. Add relevant repository topics
such as `supermemory`, `mcp`, `self-hosted`, `codex`, `claude-code`, and
`ai-memory`. Do not publish a private deployment URL as the repository home
page.

## Licensing and distribution controls

The root upstream `LICENSE` remains unchanged. The fork-created `NOTICE` remains
an accurate attribution and modification record. Nested license files remain
with their components, and modified upstream files keep prominent change
markings.

Each Ram0 runtime Docker image will include the applicable root `LICENSE` and
fork `NOTICE` under `/usr/share/licenses/ram0/`. The source repository will
state that its engine Dockerfile downloads an official, checksum-pinned
Supermemory release asset at build time.

Because the engine asset has no clear asset-specific redistribution grant:

- the public GitHub repository will contain source and build instructions only;
- GHCR images containing that binary remain private;
- no public Ram0 release will attach or mirror the binary; and
- public binary/image distribution remains blocked until Supermemory provides
  written license clarification and applicable third-party notices are audited.

This establishes compliance with the licenses visible in the source tree; it
does not represent legal advice or guarantee rights that upstream has not
documented.

## Testing and verification

Before deleting the old repository:

- add static tests asserting all runtime Dockerfiles install the required
  license and notice files;
- add documentation checks for unofficial-fork attribution and the engine
  redistribution restriction;
- run the targeted gateway, migration, integration, graph, and deployment test
  suites and type checks;
- run `git diff --check` and a secret-pattern scan limited to committed changes;
  and
- confirm the worktree contains only intended changes.

After recreation:

- verify the GitHub parent, description, topics, visibility, and default branch;
- compare local, `origin/main`, and the recorded commit IDs;
- clone the public repository into a temporary directory and verify its history,
  README, licenses, and installer paths;
- confirm GHCR engine-containing packages remain private; and
- verify the deployed service still responds without printing credentials.

## Upstream maintenance

`origin` is `olhapi/ram0`; `upstream` is
`supermemoryai/supermemory`. Future upstream updates use a dedicated sync branch
that merges or rebases the Ram0 delta onto a reviewed upstream commit, runs the
same license and test gates, and lands through a pull request. Published Ram0
release tags are immutable, and upstream license changes are reviewed before
each synchronization.
