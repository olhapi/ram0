export type AgentInstall = {
  id: "codex" | "claude-code" | "cursor" | "opencode";
  name: string;
  format: "command" | "json";
  cliInstall: string;
  persistentSetup: string;
  configVerify: string;
  directMcpSetup: string;
  directMcpNote: string;
  pluginInstall: string;
  pluginNote: string;
  migration: string;
  troubleshooting: readonly string[];
};

const REPOSITORY = "https://github.com/olhapi/ram0.git";
const CHECKOUT = "~/ram0-plugins/ram0";
const ADAPTER = "~/.local/share/ram0/mcp_stdio_adapter.py";

export const skillInstallCommand =
  "npx skills add https://github.com/olhapi/ram0 --skill ram0-memory";

export const cliInstallCommand = `git clone ${REPOSITORY} ${CHECKOUT}
python3 ${CHECKOUT}/integrations/ram0-plugin/scripts/install_cli.py`;

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
    cliInstall: cliInstallCommand,
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
      directMcpSetup: `codex mcp add ram0 -- python3 ${ADAPTER}`,
      directMcpNote:
        "Restart Codex after registering the stdio MCP connection.",
      pluginInstall: `codex plugin marketplace add ${CHECKOUT}
codex plugin add ram0@ram0-plugins`,
      pluginNote:
        "Restart Codex, open /hooks, review the bundled lifecycle hooks, and trust them.",
    },
    {
      ...common,
      id: "claude-code",
      name: "Claude Code",
      format: "command",
      directMcpSetup: `claude mcp add ram0 --scope user -- python3 ${ADAPTER}`,
      directMcpNote:
        "Restart Claude Code after registering the stdio MCP connection.",
      pluginInstall: `claude plugin marketplace add ${REPOSITORY}
claude plugin install ram0@ram0-plugins`,
      pluginNote:
        "Restart Claude Code so the Ram0 MCP registration and lifecycle hooks reload.",
    },
    {
      ...common,
      id: "cursor",
      name: "Cursor",
      format: "json",
      directMcpSetup: `{
  "mcpServers": {
    "ram0": {
      "command": "python3",
      "args": ["${ADAPTER}"]
    }
  }
}`,
      directMcpNote: "Save mcp.json and fully reload Cursor.",
      pluginInstall: `# Cursor: Settings > Plugins > Add Marketplace
# Select ${CHECKOUT}/.cursor-plugin/marketplace.json, then install Ram0.`,
      pluginNote:
        "Fully reload Cursor so its Ram0 MCP registration and lifecycle hooks start.",
    },
    {
      ...common,
      id: "opencode",
      name: "OpenCode",
      format: "json",
      directMcpSetup: `{
  "mcp": {
    "ram0": {
      "type": "local",
      "command": ["python3", "${ADAPTER}"],
      "enabled": true
    }
  }
}`,
      directMcpNote: "Save opencode.json and restart OpenCode.",
      pluginInstall: `cd ${CHECKOUT}/integrations/ram0-plugin/.opencode-plugin
bun install --frozen-lockfile && bun run build
opencode plugin "file://$PWD" --global`,
      pluginNote: "Restart OpenCode after registering the local Ram0 plugin.",
    },
  ];
}
