# Reclassification Live Impact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual historical-reclassification previews with an always-visible, automatically refreshed impact summary that uses loading skeletons.

**Architecture:** Extract rate parsing and preview-request derivation into a small pure TypeScript module so the dashboard can test the contract with Node's built-in test runner and no new dependency. Refactor `ReclassificationPanel` to drive a debounced request lifecycle keyed by the derived request, render one stable impact surface for loading/success/validation/error states, and keep execution explicitly gated by the latest successful preview.

**Tech Stack:** Next.js 15, React 19, TypeScript, existing Tailwind/Radix UI primitives, Axios wrapper, Node built-in test runner, Playwright CLI for browser acceptance.

## Global Constraints

- Change only the self-hosted dashboard; do not change REST contracts, worker behavior, category classification, job persistence, or the catalog editor.
- Use a 450ms preview debounce.
- Replace every metric value with an existing `Skeleton` during refresh; never retain stale numbers while loading.
- Use the repository's existing dependencies only; add no libraries or package scripts.
- Preserve explicit confirmation and durable execution semantics.
- Use the approved local HTML mockup as the visual reference.

---

### Task 1: Preview Request Contract

**Files:**
- Create: `server/dashboard/src/app/(root)/dashboard/categories/reclassification-preview.ts`
- Create: `tests/dashboard/reclassification-preview.test.ts`

**Interfaces:**
- Produces: `parseRate(value: string): number | null`, `derivePreviewRequest(scope, inputRate, outputRate): PreviewRequestState`, and `PREVIEW_DEBOUNCE_MS = 450`.
- `PreviewRequestState` is a discriminated union: `{ valid: false; message: string }` or `{ valid: true; key: string; payload: { scope; input_rate_per_million?: number; output_rate_per_million?: number } }`.

- [ ] **Step 1: Write failing built-in Node tests**

```ts
import assert from "node:assert/strict";
import test from "node:test";
import {
  derivePreviewRequest,
  PREVIEW_DEBOUNCE_MS,
} from "../../server/dashboard/src/app/(root)/dashboard/categories/reclassification-preview.ts";

test("uses a 450ms debounce", () => {
  assert.equal(PREVIEW_DEBOUNCE_MS, 450);
});

test("omits empty rates", () => {
  assert.deepEqual(derivePreviewRequest("unclassified_failed", "", ""), {
    valid: true,
    key: "unclassified_failed::",
    payload: { scope: "unclassified_failed" },
  });
});

test("includes a valid rate pair", () => {
  assert.deepEqual(derivePreviewRequest("all", "1.5", "2"), {
    valid: true,
    key: "all:1.5:2",
    payload: {
      scope: "all",
      input_rate_per_million: 1.5,
      output_rate_per_million: 2,
    },
  });
});

test("rejects unpaired, negative, and non-finite rates", () => {
  for (const rates of [["1", ""], ["-1", "2"], ["Infinity", "2"]]) {
    assert.equal(
      derivePreviewRequest("all", rates[0], rates[1]).valid,
      false,
    );
  }
});
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `node --experimental-strip-types --test tests/dashboard/reclassification-preview.test.ts`

Expected: FAIL because `reclassification-preview.ts` does not exist.

- [ ] **Step 3: Implement the pure request derivation**

```ts
export const PREVIEW_DEBOUNCE_MS = 450;

export function parseRate(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : Number.NaN;
}

export function derivePreviewRequest(scope, inputRate, outputRate) {
  const input = parseRate(inputRate);
  const output = parseRate(outputRate);
  const invalid = Number.isNaN(input) || Number.isNaN(output);
  const paired = (input === null && output === null) ||
    (input !== null && output !== null);
  if (invalid || !paired) {
    return { valid: false, message: "Enter both token rates as nonnegative finite numbers." };
  }
  return {
    valid: true,
    key: `${scope}:${input ?? ""}:${output ?? ""}`,
    payload: {
      scope,
      ...(input !== null && output !== null
        ? { input_rate_per_million: input, output_rate_per_million: output }
        : {}),
    },
  };
}
```

Add exact union types and the existing scope literal type around this implementation.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run: `node --experimental-strip-types --test tests/dashboard/reclassification-preview.test.ts`

Expected: all tests pass with no third-party test dependency.

- [ ] **Step 5: Commit the helper and tests**

```bash
git add 'server/dashboard/src/app/(root)/dashboard/categories/reclassification-preview.ts' tests/dashboard/reclassification-preview.test.ts
git commit -m "test(dashboard): define live preview request contract"
```

### Task 2: Debounced Live Impact Panel

**Files:**
- Modify: `server/dashboard/src/app/(root)/dashboard/categories/reclassification-panel.tsx`
- Reuse: `server/dashboard/src/components/ui/skeleton.tsx`

**Interfaces:**
- Consumes: `derivePreviewRequest` and `PREVIEW_DEBOUNCE_MS` from Task 1.
- Preserves: `ReclassificationPanelProps`, preview/execute REST payloads, category job polling, and `onReclassified()`.

- [ ] **Step 1: Record static RED assertions for the old interaction**

Run:

```bash
rg -n 'Preview reclassification|handlePreview|previewIsStale' 'server/dashboard/src/app/(root)/dashboard/categories/reclassification-panel.tsx'
rg -n 'Skeleton|Live impact|PREVIEW_DEBOUNCE_MS' 'server/dashboard/src/app/(root)/dashboard/categories/reclassification-panel.tsx'
```

Expected: the obsolete manual-preview symbols are present and the new live-impact/skeleton symbols are absent.

- [ ] **Step 2: Replace manual preview state with keyed automatic state**

Use a state shape that distinguishes loading, success, and error while retaining the request key:

```ts
type PreviewState =
  | { status: "loading"; key: string }
  | { status: "success"; key: string; result: ReclassificationPreview }
  | { status: "error"; key: string; message: string };
```

Derive the current request with `useMemo`. On every valid input change, synchronously set `{status: "loading", key}` and clear confirmation, then schedule the POST after `PREVIEW_DEBOUNCE_MS`. Use an incrementing request sequence and effect cleanup so only the latest request may set success/error. Invalid input cancels the timer, clears confirmation, and does not POST.

- [ ] **Step 3: Add immediate Retry without changing inputs**

Maintain a `retryVersion` counter. The Retry button increments it; include it in the preview effect dependencies so the current valid request runs immediately when retrying rather than waiting for another input edit. Automatic failures remain inline and do not toast.

- [ ] **Step 4: Render the stable live-impact surface**

```tsx
<section
  aria-busy={preview?.status === "loading"}
  aria-labelledby="reclassification-impact-title"
  className="overflow-hidden rounded-lg border border-memBorder-primary bg-surface-default-fg-secondary"
>
  <div className="border-b border-memBorder-primary px-3 py-2">
    <h3 id="reclassification-impact-title" className="text-xs font-semibold uppercase tracking-wide text-onSurface-default-tertiary">
      Live impact
    </h3>
  </div>
  {/* loading: five semantic metric cells whose values are Skeletons */}
  {/* success: five values in the same cells */}
  {/* validation/error: stable inline alert, Retry only for request errors */}
</section>
```

Use `grid-cols-2 md:grid-cols-5`. Each metric cell keeps a fixed minimum height. During loading render five `Skeleton` instances with `aria-hidden="true"` plus an `sr-only` “Updating live impact” status.

- [ ] **Step 5: Rebuild confirmation and action footer**

Place the checkbox/label and Start button in a responsive `flex-col md:flex-row md:items-center md:justify-between` row. Clear confirmation on edits. Enable confirmation only for the latest successful preview with `eligible_memories > 0`. Disable Start for loading, invalid inputs, zero results, missing confirmation, or execution.

- [ ] **Step 6: Preserve job history behind its divider**

Keep the existing polling, error, empty, and job-row behavior unchanged. Keep the section after the execution footer with `border-t border-memBorder-primary pt-4`.

- [ ] **Step 7: Run focused and dashboard verification**

Run:

```bash
node --experimental-strip-types --test tests/dashboard/reclassification-preview.test.ts
pnpm --dir server/dashboard exec prettier --check 'src/app/(root)/dashboard/categories/reclassification-panel.tsx' 'src/app/(root)/dashboard/categories/reclassification-preview.ts'
pnpm --dir server/dashboard run typecheck
pnpm --dir server/dashboard run build
git diff --check
```

Expected: focused tests, formatting, typecheck, build, and whitespace checks pass.

- [ ] **Step 8: Commit the panel implementation**

```bash
git add 'server/dashboard/src/app/(root)/dashboard/categories/reclassification-panel.tsx'
git commit -m "feat(dashboard): show live reclassification impact"
```

### Task 3: Running Browser Acceptance

**Files:**
- Verify only; no planned product source changes.

**Interfaces:**
- Consumes: the completed panel and existing isolated Ram0 container stack.
- Produces: screenshots and observed request/state evidence for handoff.

- [ ] **Step 1: Start the isolated category acceptance stack**

Use `server/docker-compose.categories-test.yaml` with the existing project name and guard conventions. Confirm the API and dashboard are healthy before browser actions.

- [ ] **Step 2: Verify the initial loading and loaded states**

Open `/dashboard/categories`, scroll to Reclassify historical memories, and confirm the live-impact panel is visible without clicking Preview. Capture the skeleton state while the automatic request is pending and the loaded values afterward.

- [ ] **Step 3: Verify debounce and stale-state replacement**

Change scope and rates rapidly. Confirm only the settled inputs produce the displayed result, all five prior values are replaced by skeletons during refresh, and no stale numbers remain visible.

- [ ] **Step 4: Verify validation, error, retry, and execution gating**

Confirm an unpaired rate shows inline validation and does not enable confirmation. Confirm zero eligible results disable execution. Exercise a preview error through the existing test stub if supported, verify inline Retry, then verify a successful current preview permits confirmation and Start.

- [ ] **Step 5: Compare against the approved local HTML reference**

At 1280px width, compare the configuration → Live impact → execution → recent jobs hierarchy, five-column desktop metric band, spacing, borders, skeleton dimensions, and responsive behavior.

- [ ] **Step 6: Tear down and prove cleanup**

Stop the exact Compose project with volumes, then verify its containers, volumes, network, and acceptance lock are absent.

- [ ] **Step 7: Final commit and publication**

If browser acceptance required no fixes, push the already committed changes on `main` to `origin/main`. If it exposed a defect, first add the narrowest regression evidence, fix it, rerun Tasks 2 and 3, commit conventionally, then push.
