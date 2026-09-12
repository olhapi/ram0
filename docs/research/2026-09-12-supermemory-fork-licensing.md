# Supermemory fork licensing and distribution review

Date: 2026-09-12

> This is a technical licensing review, not legal advice. It records what the
> cited project materials say and flags questions that those materials do not
> answer.

## Scope

This review covers:

- maintaining this repository as a separate fork of
  `supermemoryai/supermemory`;
- source and nested-component licenses visible in the upstream repository;
- the prebuilt `supermemory-server` v0.0.6 release asset used by this fork;
- the fork's `LICENSE`, `NOTICE`, change markings, and three runtime
  Dockerfiles; and
- the minimum steps needed before distributing source or container images.

It does not audit every transitive dependency, model, model weight, base image,
font, or media asset. Those need a generated third-party license/SBOM review
before public distribution.

## Bottom line

| Item | Finding | Status |
|---|---|---|
| Forking and modifying the upstream source | The upstream root is MIT-licensed. The license permits use, modification, publication, distribution, sublicensing, and sale, provided the upstream copyright and permission notice remains in all copies or substantial portions. | Clear, subject to preserving the root license |
| Publishing a separate source fork | GitHub permits forks of public repositories, and the MIT grant independently permits modification and distribution. Keep the upstream license, identify the fork and changes, and avoid implying upstream endorsement. | Clear with attribution and distinct branding |
| Upstream `NOTICE` obligation | The upstream tree does not contain a root `NOTICE`; MIT itself does not require a file named `NOTICE`. This fork's root `NOTICE` is an added attribution/change record, not an upstream-imposed requirement. | Optional but useful; keep it accurate |
| Nested `skills/supermemory` component | This directory has its own Apache-2.0 license. Distribution requires a copy of that license, retention of applicable notices, and prominent change notices in modified files. Apache-2.0 expressly does not grant trademark use except customary origin descriptions. | Clear; nested license must travel with the component |
| Prebuilt `supermemory-server` v0.0.6 binary | It is an official release asset associated with a tag whose repository root is MIT, and the tag's self-hosting docs call the binary open source. However, neither the release page, installer, nor manifest states an asset-specific license; the tagged repository tree does not expose identifiable server implementation source. | **Not sufficiently explicit for confident binary redistribution** |
| Runtime notice baseline and remediation | At the initial review baseline, the three runtime stages omitted explicit root notice copies. The repository now copies `LICENSE` and `NOTICE` into all three runtime stages under `/usr/share/licenses/ram0/`. | Source-level remediation verified; binary-license and third-party inventory gates remain open |
| Third-party dependency notices | The JavaScript bundles and base images include third-party code, but this review did not produce a complete dependency license inventory. | Audit before public distribution |

## 1. Upstream source license

The current upstream root `LICENSE` is MIT, copyright 2025 supermemory. Its
grant includes using, copying, modifying, merging, publishing, distributing,
sublicensing, and selling the software. Its sole operative condition is that
the copyright and permission notice be included in all copies or substantial
portions. The same MIT text is present at the exact v0.0.6 release tag commit,
`566be208981aa23ef20a85fd50a737861b1b10b2`.

Primary sources:

- [Current upstream root license](https://github.com/supermemoryai/supermemory/blob/main/LICENSE)
- [Root license at the v0.0.6 tag commit](https://github.com/supermemoryai/supermemory/blob/566be208981aa23ef20a85fd50a737861b1b10b2/LICENSE)
- [Canonical MIT text and identifier from SPDX](https://spdx.org/licenses/MIT.html)

Practical obligations for this fork's source distribution:

1. Retain the upstream root `LICENSE` and its copyright statement verbatim.
2. Do not replace the upstream copyright with only the fork author's
   copyright. Add the fork copyright separately, such as in `NOTICE` and in
   new-file SPDX headers.
3. Include the root license in source archives and other distributions that
   contain substantial portions of upstream code.
4. MIT does not require disclosure of source, use of the same license for the
   fork's new code, or a particular GitHub fork relationship. This fork has
   nevertheless chosen MIT for its additions, which keeps the combined source
   straightforward.

### Earlier license history

The repository changed its root license from CC BY-NC-SA 4.0 to MIT in commit
`6e006502e5d0ee39979b64bcd1f2140867302d09` on 2025-08-16. The v0.0.6 tag is
later and contains the MIT license. A complete legal provenance audit would
still need to confirm that the upstream project had authority to relicense all
pre-change third-party contributions; this review did not examine contributor
agreements or individual contribution histories.

Primary source:

- [Upstream license-change commit](https://github.com/supermemoryai/supermemory/commit/6e006502e5d0ee39979b64bcd1f2140867302d09)

## 2. Nested component licenses

The repository is predominantly under the root MIT license. Several packages
repeat an MIT license, including the Python agent framework, OpenAI-compatible
Python SDK, and tools package. The memory-graph package also declares `MIT` in
its package metadata.

One material exception is `skills/supermemory`, which contains an Apache-2.0
license. Apache-2.0 section 4 requires recipients to receive the license,
modified files to carry prominent change notices, and applicable copyright,
patent, trademark, and attribution notices to remain. A downstream `NOTICE`
copy is required only if the Apache-licensed Work itself includes a `NOTICE`
file; the upstream skill directory contains a license but no `NOTICE` file.
Apache-2.0 section 6 expressly withholds trademark permission except for
reasonable, customary descriptions of the Work's origin.

Primary sources:

- [`skills/supermemory/LICENSE` (Apache-2.0)](https://github.com/supermemoryai/supermemory/blob/main/skills/supermemory/LICENSE)
- [`packages/memory-graph/package.json` (`license: MIT`)](https://github.com/supermemoryai/supermemory/blob/main/packages/memory-graph/package.json)
- [`packages/tools/LICENSE` (MIT)](https://github.com/supermemoryai/supermemory/blob/main/packages/tools/LICENSE)
- [`packages/agent-framework-python/LICENSE` (MIT)](https://github.com/supermemoryai/supermemory/blob/main/packages/agent-framework-python/LICENSE)
- [`packages/openai-sdk-python/LICENSE` (MIT)](https://github.com/supermemoryai/supermemory/blob/main/packages/openai-sdk-python/LICENSE)

The current fork does not modify files under `skills/supermemory` relative to
upstream. If it later does, those modified skill files should carry prominent
change notices and the Apache license must remain with the distributed skill.

## 3. `NOTICE` and change marking in this checkout

This checkout retains the upstream MIT `LICENSE` unchanged. Its root `NOTICE`
is fork-created and states that the distribution derives from Supermemory,
summarizes the Ram0-owned additions, and points to repository history for
changes. It also records that the engine image downloads a separately released
binary and pins its version and digest.

The three modified upstream graph files inspected in this review begin with a
`Modified for Ram0` notice. Newly added TypeScript, shell, and Docker files
generally carry `SPDX-FileCopyrightText: 2026 Ram0 contributors` and
`SPDX-License-Identifier: MIT` headers. That is a useful provenance convention,
although MIT itself does not require per-file change notices.

The current `NOTICE` should not say or imply that Supermemory endorses the
fork. A conservative public identity is "Ram0, an unofficial fork derived from
Supermemory" rather than presenting the product as official Supermemory.

## 4. Prebuilt server binary: unresolved redistribution terms

`deploy/supermemory/Dockerfile.engine` downloads
`supermemory-server-linux-x64` from the official `server-v0.0.6` GitHub release
and verifies SHA-256
`bb1b7cee393818236873b8e2518a435e10d9195e27ea5608a3af48a733ef8ee8`.
The official v0.0.6 manifest reports the same checksum.

Positive evidence:

- The asset is published by the official upstream repository.
- The release points to signed tag commit `566be20`, whose root license is MIT.
- The self-hosting documentation at that tag describes the local binary as
  "free, open source" and links the repository.

Missing or conflicting evidence:

- The v0.0.6 release page does not identify a separate license for binary
  assets.
- The release's `install.sh` downloads and verifies the executable but neither
  displays nor installs a license file.
- The release's `manifest.json` contains only the version and platform
  checksums; it has no license field.
- Inspection of the tagged Git tree found the root/nested licenses and
  self-hosting docs but no clearly identifiable source tree for the server
  implementation that produced the executable.
- The later v0.0.7 release says self-hosted "is licensed up to 10,000
  documents" and links `git.new/memory`, while the repository root remains
  MIT. A technical document cap is not inherently inconsistent with MIT, but
  the word "licensed" indicates that downstream distributors should obtain
  explicit clarification rather than assume every release asset is governed
  only by the root license.

Primary sources:

- [Official v0.0.6 release](https://github.com/supermemoryai/supermemory/releases/tag/server-v0.0.6)
- [Official v0.0.6 installer asset](https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.6/install.sh)
- [Official v0.0.6 manifest asset](https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.6/manifest.json)
- [Self-hosting overview at the v0.0.6 tag](https://github.com/supermemoryai/supermemory/blob/566be208981aa23ef20a85fd50a737861b1b10b2/apps/docs/self-hosting/overview.mdx)
- [v0.0.6 tagged source tree](https://github.com/supermemoryai/supermemory/tree/566be208981aa23ef20a85fd50a737861b1b10b2)
- [Official v0.0.7 release containing the 10,000-document statement](https://github.com/supermemoryai/supermemory/releases/tag/server-v0.0.7)

Recommendation: do not publicly redistribute an image containing the engine
binary until Supermemory confirms in writing that the relevant binary release
asset is under MIT (or supplies its actual binary license). For private local
deployment, the lower-risk structure is to have the operator's machine fetch
the official, checksum-pinned asset directly rather than republishing that
asset through a downstream registry. This is a risk-reduction recommendation,
not a conclusion that the current use infringes.

## 5. Runtime-container notice audit: historical baseline and remediation

At the initial review baseline (before repository-rebase compliance changes),
static inspection found no explicit copy of the fork's root `LICENSE` or
`NOTICE` into any runtime image:

- `deploy/supermemory/Dockerfile.engine` copies only the downloaded executable
  from its download stage.
- `apps/local-gateway/Dockerfile` copies only bundled `server.js` and
  `migrate.js` into its runtime stage.
- `apps/memory-graph-playground/Dockerfile` copies the Next.js standalone
  output, static assets, and public assets into its runtime stage.

Those baseline Dockerfiles did not guarantee inclusion of the upstream MIT
notice or the fork's notice. Remediation now present in all three runtime
Dockerfiles is `COPY LICENSE NOTICE /usr/share/licenses/ram0/`, checked by
`deploy/supermemory/tests/test-public-source-compliance.sh`. This is static
source evidence, not a claim that previously built/deployed images contain
those files. Distribution still requires:

- the root `LICENSE` containing the upstream MIT copyright/permission notice;
- the root `NOTICE` describing the fork; and
- a generated third-party license inventory appropriate to each image.

The chosen root-notice target is `/usr/share/licenses/ram0/`. Image labels can
add provenance links but should not replace the required license text. The
third-party inventory and exact engine-binary license remain unresolved;
engine-containing packages must remain private pending written clarification
and the applicable notice audit.

## 6. Branding and trademark caution

The upstream root MIT license grants copyright permissions; it does not state
a trademark license. No Supermemory trademark or brand-use policy was found in
the inspected upstream tree. The nested Apache-2.0 skill license is explicit:
its section 6 grants no right to use contributor trade names, trademarks,
service marks, or product names except reasonable and customary origin
descriptions and reproduction of a NOTICE.

Therefore:

- use a distinct fork name and visual identity;
- describe provenance factually (for example, "derived from Supermemory");
- do not use upstream logos as the fork's logo or imply sponsorship,
  certification, or official status; and
- retain upstream names where technically necessary for package names, API
  compatibility, attribution, and origin descriptions.

This is conservative risk management because the root MIT text is silent on
trademarks; it is not a finding that a particular upstream mark is registered
in a particular jurisdiction.

Primary sources:

- [Supermemory's official product site](https://supermemory.ai/)
- [USPTO: What is a trademark?](https://www.uspto.gov/trademarks/basics/what-trademark)

## 7. Separate-fork maintenance model

GitHub documents the standard arrangement: `origin` points to the fork and an
`upstream` remote points to the source repository. GitHub also confirms that a
public repository can be forked and that public source requires a license for
rights beyond GitHub's platform-level viewing/forking permission.

Primary sources:

- [GitHub: Configuring a remote repository for a fork](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/configuring-a-remote-repository-for-a-fork)
- [GitHub: Syncing a fork](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/syncing-a-fork)
- [GitHub: Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

Recommended repository policy:

1. Keep the fork in a dedicated repository with `origin` under the fork
   owner's control and `upstream` set to
   `https://github.com/supermemoryai/supermemory.git`.
2. Keep an unmodified upstream-tracking branch or rely on the read-only
   `upstream/main` remote-tracking ref. Put local product changes on the fork's
   `main` branch as a small, reviewable commit series above a recorded upstream
   base.
3. For each update, record the old and new upstream commit IDs, apply or rebase
   only the fork delta, run tests and the license audit, and merge through a
   reviewed synchronization branch. Never rewrite published release tags.
4. Preserve `LICENSE`, the nested component licenses, and an accurate fork
   `NOTICE` in every source release.
5. Do not publish engine-containing images until the binary-license question
   is resolved. Once resolved, include the applicable engine license and all
   source/dependency notices in the images and release artifacts.

## 8. Release gate

Before calling a public distribution legally ready, require all of the
following:

- [ ] Root MIT `LICENSE` retained unchanged.
- [ ] Fork attribution and modification record current.
- [ ] Apache-licensed skill still includes its license; modified files, if any,
      are prominently marked.
- [ ] Root and applicable nested/third-party licenses copied into each runtime
      image and source archive.
- [ ] Dependency/license inventory generated for bundled JavaScript, native
      packages, base images, models, weights, fonts, and media.
- [ ] Written clarification or an explicit license obtained for the exact
      `supermemory-server` release asset being redistributed.
- [ ] Fork branding is distinct and does not imply upstream endorsement.

Until the last four items are complete, the source fork is maintainable under
the visible licenses, but the downstream runtime-image distribution should not
be represented as fully cleared.
