"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import {
  CategoryJob,
  ReclassificationPreview,
  ReclassificationStartResponse,
} from "@/types/api";
import { api } from "@/utils/api";
import { CATEGORY_ENDPOINTS } from "@/utils/api-endpoints";
import {
  derivePreviewRequest,
  PREVIEW_DEBOUNCE_MS,
  ReclassificationScope,
} from "./reclassification-preview";

type PreviewState =
  | { status: "loading"; key: string }
  | { status: "success"; key: string; result: ReclassificationPreview }
  | { status: "error"; key: string; message: string };

interface ReclassificationPanelProps {
  disabled?: boolean;
  onReclassified: () => void;
}

const ACTIVE_JOB_STATES = new Set<CategoryJob["state"]>([
  "queued",
  "processing",
  "retrying",
]);

function jobStateBadge(state: CategoryJob["state"]) {
  const variant = state === "failed" ? "destructive" : "outline";
  return <Badge variant={variant}>{state}</Badge>;
}

function jobDiagnostic(job: CategoryJob): string | null {
  if (!job.error_code && !job.error_message) return null;
  return [job.error_code, job.error_message].filter(Boolean).join(": ");
}

export function ReclassificationPanel({
  disabled = false,
  onReclassified,
}: ReclassificationPanelProps) {
  const [scope, setScope] = useState<ReclassificationScope>(
    "unclassified_failed",
  );
  const [inputRate, setInputRate] = useState("");
  const [outputRate, setOutputRate] = useState("");
  const [preview, setPreview] = useState<PreviewState>();
  const [retryVersion, setRetryVersion] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [jobs, setJobs] = useState<CategoryJob[]>([]);
  const [jobsError, setJobsError] = useState("");
  const [isLoadingJobs, setIsLoadingJobs] = useState(false);
  const [jobsRefreshVersion, setJobsRefreshVersion] = useState(0);
  const jobsRequestInFlight = useRef(false);
  const jobsRefreshQueued = useRef(false);
  const previewRequestSequence = useRef(0);
  const retryImmediately = useRef(false);
  const unmounted = useRef(false);

  const previewRequest = useMemo(
    () => derivePreviewRequest(scope, inputRate, outputRate),
    [inputRate, outputRate, scope],
  );
  const currentRequestKey = previewRequest.valid ? previewRequest.key : "";
  const hasCurrentPreview =
    preview?.status === "success" && preview.key === currentRequestKey;
  const isPreviewing =
    preview?.status === "loading" && preview.key === currentRequestKey;
  const currentPreview = hasCurrentPreview ? preview.result : undefined;
  const hasRatePair =
    previewRequest.valid &&
    previewRequest.payload.input_rate_per_million !== undefined;
  const hasActiveJobs = jobs.some((job) => ACTIVE_JOB_STATES.has(job.state));

  const requestJobs = useCallback(async () => {
    if (unmounted.current) return;
    if (jobsRequestInFlight.current) {
      jobsRefreshQueued.current = true;
      return;
    }

    jobsRequestInFlight.current = true;
    setIsLoadingJobs(true);
    try {
      const response = await api.get<CategoryJob[]>(CATEGORY_ENDPOINTS.JOBS, {
        params: { limit: 50 },
      });
      if (!unmounted.current) {
        setJobs(Array.isArray(response.data) ? response.data : []);
        setJobsError("");
      }
    } catch {
      if (!unmounted.current) {
        setJobsError("Could not refresh category jobs.");
      }
    } finally {
      jobsRequestInFlight.current = false;
      if (!unmounted.current) {
        setIsLoadingJobs(false);
        setJobsRefreshVersion((version) => version + 1);
      }
      if (jobsRefreshQueued.current && !unmounted.current) {
        jobsRefreshQueued.current = false;
        void Promise.resolve().then(() => requestJobs());
      }
    }
  }, []);

  useEffect(() => {
    unmounted.current = false;
    void requestJobs();
    return () => {
      unmounted.current = true;
    };
  }, [requestJobs]);

  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setTimeout(() => void requestJobs(), 2_000);
    return () => window.clearTimeout(timer);
  }, [hasActiveJobs, jobsRefreshVersion, requestJobs]);

  useEffect(() => {
    if (disabled || !previewRequest.valid) {
      setPreview(undefined);
      setConfirmed(false);
      return;
    }

    const { key, payload } = previewRequest;
    const sequence = ++previewRequestSequence.current;
    const delay = retryImmediately.current ? 0 : PREVIEW_DEBOUNCE_MS;
    retryImmediately.current = false;
    setPreview({ status: "loading", key });
    setConfirmed(false);

    const timer = window.setTimeout(async () => {
      try {
        const response = await api.post<ReclassificationPreview>(
          CATEGORY_ENDPOINTS.PREVIEW,
          payload,
        );
        if (!unmounted.current && previewRequestSequence.current === sequence) {
          setPreview({ status: "success", key, result: response.data });
        }
      } catch (error) {
        if (!unmounted.current && previewRequestSequence.current === sequence) {
          setPreview({
            status: "error",
            key,
            message: getErrorMessage(error, "Could not update live impact"),
          });
        }
      }
    }, delay);

    return () => {
      window.clearTimeout(timer);
      if (previewRequestSequence.current === sequence) {
        previewRequestSequence.current += 1;
      }
    };
  }, [disabled, previewRequest, retryVersion]);

  const retryPreview = () => {
    retryImmediately.current = true;
    setRetryVersion((version) => version + 1);
  };

  const handleExecute = async () => {
    if (!hasCurrentPreview || !confirmed) return;

    setIsExecuting(true);
    try {
      const response = await api.post<ReclassificationStartResponse>(
        CATEGORY_ENDPOINTS.EXECUTE,
        { scope, confirm: "RECLASSIFY" },
      );
      setConfirmed(false);
      toast({
        title: "Reclassification started",
        description: `${response.data.created_jobs} jobs created; ${response.data.skipped_active_jobs} active jobs retained.`,
        variant: "success",
      });
      onReclassified();
      void requestJobs();
    } catch (error) {
      toast({
        title: "Failed to start reclassification",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setIsExecuting(false);
    }
  };

  const executionDisabled =
    disabled ||
    isExecuting ||
    isPreviewing ||
    !confirmed ||
    !hasCurrentPreview ||
    !previewRequest.valid ||
    !currentPreview ||
    currentPreview.eligible_memories === 0;

  const changeRequest = (next: () => void) => {
    next();
    setConfirmed(false);
  };

  const impactMetrics = currentPreview
    ? [
        [
          "Eligible memories",
          currentPreview.eligible_memories.toLocaleString(),
        ],
        ["Classifier calls", currentPreview.estimated_calls.toLocaleString()],
        [
          "Input tokens",
          currentPreview.estimated_input_tokens.toLocaleString(),
        ],
        [
          "Output tokens",
          currentPreview.estimated_output_tokens.toLocaleString(),
        ],
        [
          "Estimated cost",
          hasRatePair && currentPreview.estimated_cost !== null
            ? String(currentPreview.estimated_cost)
            : "Unavailable",
        ],
      ]
    : undefined;

  return (
    <Card className="border-memBorder-primary">
      <CardHeader className="gap-2">
        <CardTitle className="text-sm">
          Reclassify historical memories
        </CardTitle>
        <p className="text-sm text-onSurface-default-tertiary">
          Estimates update automatically. Reclassification only starts after
          explicit confirmation.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="reclassification-scope">Scope</Label>
            <select
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              disabled={disabled || isExecuting}
              id="reclassification-scope"
              onChange={(event) =>
                changeRequest(() =>
                  setScope(event.target.value as ReclassificationScope),
                )
              }
              value={scope}
            >
              <option value="unclassified_failed">
                Unclassified and failed
              </option>
              <option value="all">All memories</option>
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="reclassification-input-rate">
              Input rate per million tokens (optional)
            </Label>
            <Input
              aria-describedby={
                previewRequest.valid ? undefined : "reclassification-rate-error"
              }
              aria-invalid={!previewRequest.valid}
              disabled={disabled || isExecuting}
              id="reclassification-input-rate"
              min="0"
              onChange={(event) =>
                changeRequest(() => setInputRate(event.target.value))
              }
              placeholder="0.00"
              step="any"
              type="number"
              value={inputRate}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="reclassification-output-rate">
              Output rate per million tokens (optional)
            </Label>
            <Input
              aria-describedby={
                previewRequest.valid ? undefined : "reclassification-rate-error"
              }
              aria-invalid={!previewRequest.valid}
              disabled={disabled || isExecuting}
              id="reclassification-output-rate"
              min="0"
              onChange={(event) =>
                changeRequest(() => setOutputRate(event.target.value))
              }
              placeholder="0.00"
              step="any"
              type="number"
              value={outputRate}
            />
          </div>
        </div>

        <section
          aria-busy={isPreviewing}
          aria-labelledby="reclassification-impact-title"
          className="overflow-hidden rounded-lg border border-memBorder-primary bg-surface-default-fg-secondary"
        >
          <div className="border-b border-memBorder-primary px-3 py-2">
            <h3
              className="text-xs font-semibold tracking-wide text-onSurface-default-tertiary uppercase"
              id="reclassification-impact-title"
            >
              Live impact
            </h3>
          </div>

          {!previewRequest.valid ? (
            <p
              className="px-3 py-4 text-sm text-onSurface-danger-primary"
              id="reclassification-rate-error"
              role="alert"
            >
              {previewRequest.message}
            </p>
          ) : preview?.status === "error" &&
            preview.key === currentRequestKey ? (
            <div
              className="flex flex-wrap items-center justify-between gap-3 px-3 py-4"
              role="alert"
            >
              <p className="text-sm text-onSurface-danger-primary">
                {preview.message}
              </p>
              <Button
                disabled={disabled || isExecuting}
                onClick={retryPreview}
                size="sm"
                type="button"
                variant="outline"
              >
                Retry
              </Button>
            </div>
          ) : (
            <>
              {isPreviewing && (
                <span className="sr-only" role="status">
                  Updating live impact
                </span>
              )}
              <dl className="grid grid-cols-2 divide-x divide-y divide-memBorder-primary text-sm md:grid-cols-5 md:divide-y-0">
                {(
                  impactMetrics ?? [
                    ["Eligible memories", ""],
                    ["Classifier calls", ""],
                    ["Input tokens", ""],
                    ["Output tokens", ""],
                    ["Estimated cost", ""],
                  ]
                ).map(([label, value], index) => (
                  <div className="min-h-16 px-3 py-3" key={label}>
                    <dt className="mb-2 text-xs text-onSurface-default-tertiary">
                      {label}
                    </dt>
                    <dd className="font-medium">
                      {impactMetrics ? (
                        value
                      ) : (
                        <Skeleton
                          aria-hidden="true"
                          className={index > 1 ? "h-4 w-16" : "h-4 w-12"}
                        />
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
              {currentPreview?.eligible_memories === 0 && (
                <p className="border-t border-memBorder-primary px-3 py-2 text-xs text-onSurface-default-tertiary">
                  No memories match this scope.
                </p>
              )}
            </>
          )}
        </section>

        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-start gap-2">
            <Checkbox
              checked={confirmed}
              disabled={
                disabled ||
                !hasCurrentPreview ||
                isExecuting ||
                currentPreview?.eligible_memories === 0
              }
              id="reclassification-confirm"
              onCheckedChange={(checked) => setConfirmed(checked === true)}
            />
            <Label
              className="cursor-pointer text-sm leading-5"
              htmlFor="reclassification-confirm"
            >
              I understand this queues durable historical reclassification work.
            </Label>
          </div>
          <Button
            className="self-end md:self-auto"
            disabled={executionDisabled}
            onClick={() => void handleExecute()}
            size="sm"
            type="button"
            variant="primary"
          >
            {isExecuting ? "Starting..." : "Start reclassification"}
          </Button>
        </div>

        <div className="space-y-3 border-t border-memBorder-primary pt-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold">Recent category jobs</h3>
              <p className="text-xs text-onSurface-default-tertiary">
                {hasActiveJobs
                  ? "Refreshing every 2 seconds while jobs are active."
                  : "No active jobs to poll."}
              </p>
            </div>
            <Button
              aria-label="Refresh category jobs"
              disabled={isLoadingJobs}
              onClick={() => void requestJobs()}
              size="icon"
              type="button"
              variant="ghost"
            >
              <RefreshCw className="size-4" />
            </Button>
          </div>
          {jobsError && (
            <p className="text-sm text-onSurface-danger-primary" role="alert">
              {jobsError}
            </p>
          )}
          {jobs.length === 0 ? (
            <p className="text-sm text-onSurface-default-tertiary">
              No category jobs yet.
            </p>
          ) : (
            <div className="space-y-2">
              {jobs.map((job) => {
                const diagnostic = jobDiagnostic(job);
                return (
                  <div
                    className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-memBorder-primary p-3 text-sm"
                    key={job.id}
                  >
                    <div className="min-w-0">
                      <p className="font-mono text-xs break-all">
                        {job.memory_id}
                      </p>
                      <p className="text-xs text-onSurface-default-tertiary">
                        {job.attempts} attempt{job.attempts === 1 ? "" : "s"}
                      </p>
                      {diagnostic && (
                        <p className="mt-1 text-xs text-onSurface-danger-primary">
                          {diagnostic}
                        </p>
                      )}
                    </div>
                    {jobStateBadge(job.state)}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
