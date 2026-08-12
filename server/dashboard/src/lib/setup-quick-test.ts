export function buildSetupQuickTestPayload(message: string) {
  return {
    messages: [{ role: "user", content: message }],
  };
}
