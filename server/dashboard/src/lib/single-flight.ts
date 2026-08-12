export function singleFlight<T>(operation: () => Promise<T>) {
  let inFlight: Promise<T> | null = null;

  return () => {
    if (!inFlight) {
      inFlight = operation().finally(() => {
        inFlight = null;
      });
    }
    return inFlight;
  };
}
