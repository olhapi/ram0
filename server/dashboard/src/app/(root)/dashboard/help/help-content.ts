export type AgentInstall = {
  id: "codex" | "claude-code";
  name: string;
  format: "command" | "json";
  persistentSetup: string;
  configVerify: string;
  pluginInstall: string;
  pluginUpdate: string;
  pluginNote: string;
  migration: string;
  troubleshooting: readonly string[];
};

const MARKETPLACE = "https://github.com/olhapi/ram0-plugins.git";

export const skillInstallCommand =
  "npx skills add https://github.com/olhapi/ram0 --skill ram0-memory";

function normalizedApiUrl(apiUrl?: string): string | null {
  const candidate = apiUrl?.trim();
  if (!candidate || /[\s'\"`$\\]/.test(candidate)) return null;
  try {
    const parsed = new URL(candidate);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash
    )
      return null;
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    return null;
  }
}

export function mcpUrl(apiUrl?: string): string | null {
  const base = normalizedApiUrl(apiUrl);
  return base ? `${base}/mcp` : null;
}

export function persistentSetupCommand(apiUrl: string): string {
  const base = normalizedApiUrl(apiUrl);
  if (!base) throw new Error("A valid Ram0 API URL is required.");
  return `ram0 setup --url '${base}'`;
}

export function agentInstalls(apiUrl?: string): AgentInstall[] {
  const base = normalizedApiUrl(apiUrl);
  if (!base) return [];
  const common = {
    persistentSetup: persistentSetupCommand(base),
    configVerify: "ram0 config test",
    migration:
      "Remove the old mem0-plugins marketplace entry and any same-named remote Ram0 MCP connection before installing ram0@ram0-plugins.",
    troubleshooting: [
      "Missing configuration: run ram0 setup again.",
      "Unsafe permissions: chmod 600 ~/.config/ram0/config.json",
      "Rotate a key: ram0 config set-key",
      "Unreachable endpoint: check the stored URL with ram0 config show, then run ram0 config test.",
    ],
  } as const;

  return [
    {
      ...common,
      id: "codex",
      name: "Codex",
      format: "command",
      pluginInstall: `codex plugin marketplace add ${MARKETPLACE}
codex plugin add ram0@ram0-plugins`,
      pluginUpdate: "codex plugin marketplace upgrade ram0-plugins",
      pluginNote:
        "Restart Codex, open /hooks, review the bundled lifecycle hooks, and trust them. The trusted session-start hook installs or refreshes the Ram0 CLI.",
    },
    {
      ...common,
      id: "claude-code",
      name: "Claude Code",
      format: "command",
      pluginInstall: `claude plugin marketplace add ${MARKETPLACE}
claude plugin install ram0@ram0-plugins`,
      pluginUpdate: `claude plugin marketplace update ram0-plugins
claude plugin update ram0@ram0-plugins`,
      pluginNote:
        "Restart Claude Code and approve the bundled lifecycle hooks when prompted. The trusted session-start hook installs or refreshes the Ram0 CLI.",
    },
  ];
}
