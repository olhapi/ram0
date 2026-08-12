export type ReclassificationScope = "unclassified_failed" | "all";

interface ReclassificationPreviewPayload {
  scope: ReclassificationScope;
  input_rate_per_million?: number;
  output_rate_per_million?: number;
}

export type PreviewRequestState =
  | {
      valid: false;
      message: string;
    }
  | {
      valid: true;
      key: string;
      payload: ReclassificationPreviewPayload;
    };

export const PREVIEW_DEBOUNCE_MS = 450;

const RATE_ERROR = "Enter both token rates as nonnegative finite numbers.";

export function parseRate(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : Number.NaN;
}

export function derivePreviewRequest(
  scope: ReclassificationScope,
  inputRate: string,
  outputRate: string,
): PreviewRequestState {
  const input = parseRate(inputRate);
  const output = parseRate(outputRate);
  const invalid = Number.isNaN(input) || Number.isNaN(output);
  const paired =
    (input === null && output === null) || (input !== null && output !== null);

  if (invalid || !paired) {
    return { valid: false, message: RATE_ERROR };
  }

  return {
    valid: true,
    key: `${scope}:${input ?? ""}:${output ?? ""}`,
    payload: {
      scope,
      ...(input !== null && output !== null
        ? {
            input_rate_per_million: input,
            output_rate_per_million: output,
          }
        : {}),
    },
  };
}
