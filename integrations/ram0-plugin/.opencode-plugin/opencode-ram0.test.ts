import {describe, expect, test} from "bun:test";
import {createHmac} from "node:crypto";
import {chmod, mkdir, mkdtemp, readFile, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {Hooks} from "@opencode-ai/plugin";
import {
  RAM0_MCP_TOOL_NAMES,
  createRam0Hooks,
  durableCandidates,
  mergeCategoryDefinitions,
  safeRetrievalQuery,
} from "./opencode-ram0.ts";
import Ram0Plugin from "./opencode-ram0.ts";
import {Ram0Client} from "./ram0-client.ts";

type Recorded = {url: string; init: RequestInit; body: unknown};
type AutomaticContextPolicy = {accepted: string[]; rejected: string[]; configured_key_template: string};

const AUTOMATIC_CONTEXT_POLICY = JSON.parse(
  await readFile(new URL("../tests/automatic_context_policy.json", import.meta.url), "utf8"),
) as AutomaticContextPolicy;

function proof(key: string, memory: string): string {
  return createHmac("sha256", key).update(`ram0-auto-context-v1\0${memory}`).digest("hex");
}

function recorder(options: {memories?: string[]; categories?: unknown; memoryKey?: string; trusted?: boolean} = {}) {
  const requests: Recorded[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({url, init: init ?? {}, body});
    if (url.endsWith("/categories") && init?.method === "GET") {
      return new Response(JSON.stringify(options.categories ?? {saved: [], active: []}), {status: 200});
    }
    if (url.endsWith("/search")) {
      const key = options.memoryKey ?? "ram0-key";
      return new Response(
        JSON.stringify({results: (options.memories ?? []).map((memory, index) => ({
          id: String(index),
          memory,
          ...(options.trusted === false ? {} : {metadata: {
            ram0_auto_context_version: "1",
            ram0_auto_context_proof: proof(key, memory),
          }}),
        }))}),
        {status: 200},
      );
    }
    return new Response(JSON.stringify({ok: true}), {status: 200});
  };
  return {requests, fetcher};
}

async function hooks(fetcher: typeof fetch, dataDir: string): Promise<Hooks> {
  return createRam0Hooks({
    environment: {RAM0_API_URL: "http://ram0.local:8888", RAM0_API_KEY: "ram0-key"},
    fetcher,
    dataDir,
    project: "repo-a",
  });
}

const userOutput = (text: string) => ({
  message: {role: "user"} as any,
  parts: [{id: "part-1", sessionID: "s1", messageID: "m1", type: "text", text}] as any[],
});

const transformed = (sessionID = "s1") => ({
  messages: [
    {
      info: {role: "user", sessionID} as any,
      parts: [{id: "part-2", sessionID, messageID: "m2", type: "text", text: "original"}] as any[],
    },
  ],
});

let assistantMessageCounter = 0;
async function assistantOutput(plugin: Hooks, text: string, sessionID = "s1") {
  const messageID = `assistant-${assistantMessageCounter += 1}`;
  await plugin.event?.({event: {
    type: "message.updated",
    properties: {info: {id: messageID, sessionID, role: "assistant"}},
  } as any});
  await plugin.event?.({event: {
    type: "message.part.updated",
    properties: {part: {id: `part-${messageID}`, sessionID, messageID, type: "text", text}},
  } as any});
}

describe("Ram0 OpenCode lifecycle", () => {
  test("exports a loadable real OpenCode plugin function", () => {
    expect(typeof Ram0Plugin).toBe("function");
  });

  test("generic and durable adds preserve distinct inference semantics", async () => {
    const recorded = recorder();
    const client = new Ram0Client(
      {apiUrl: "http://ram0.local:8888", apiKey: "ram0-key"},
      recorded.fetcher,
    );

    await client.add("Infer this", {source: "manual"});
    await client.addDurable("Persist exactly", {source: "capture"});

    expect(recorded.requests.map((request) => request.body)).toEqual([
      {messages: [{role: "user", content: "Infer this"}], metadata: {source: "manual"}},
      {messages: [{role: "user", content: "Persist exactly"}], metadata: {source: "capture"}, infer: false},
    ]);
    expect(recorded.requests.every((request) => request.init.redirect === "error")).toBe(true);
  });

  test("package types export names the declaration emitted by a clean build", async () => {
    const pkg = JSON.parse(await readFile(new URL("./package.json", import.meta.url), "utf8"));
    expect(pkg.types).toBe("dist/opencode-ram0.d.ts");
    expect(pkg.exports["."].types).toBe("./dist/opencode-ram0.d.ts");
  });

  test("config hook registers the exact six-tool Ram0 MCP with bearer authentication", async () => {
    expect(RAM0_MCP_TOOL_NAMES).toEqual([
      "remember",
      "search_memories",
      "list_memories",
      "get_memory",
      "update_memory",
      "forget_memory",
    ]);
    const plugin = await createRam0Hooks({
      environment: {RAM0_API_URL: "https://ram0.example.test", RAM0_API_KEY: "already-set-key"},
      dataDir: await mkdtemp(join(tmpdir(), "ram0-opencode-")),
    });
    const config = {} as any;

    await plugin.config?.(config);

    expect(config.mcp).toEqual({
      ram0: {
        type: "remote",
        url: "https://ram0.example.test/mcp",
        enabled: true,
        headers: {Authorization: "Bearer already-set-key"},
        oauth: false,
      },
    });
    expect(plugin.tool).toBeUndefined();
  });

  test("config hook and lifecycle share persistent file configuration", async () => {
    const home = await mkdtemp(join(tmpdir(), "ram0-opencode-home-"));
    const configDirectory = join(home, ".config", "ram0");
    await mkdir(configDirectory, {recursive: true, mode: 0o700});
    await chmod(configDirectory, 0o700);
    const configPath = join(configDirectory, "config.json");
    await writeFile(
      configPath,
      JSON.stringify({api_url: "https://persistent.example", api_key: "persistent-key"}),
      {mode: 0o600},
    );
    await chmod(configPath, 0o600);
    const plugin = await createRam0Hooks({environment: {}, home, dataDir: join(home, "data")});
    const config = {} as any;

    await plugin.config?.(config);

    expect(config.mcp.ram0.url).toBe("https://persistent.example/mcp");
    expect(config.mcp.ram0.headers).toEqual({Authorization: "Bearer persistent-key"});
  });

  test("disabled retrieval and capture toggles prevent their network operations", async () => {
    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const recorded = recorder({memories: ["must not load"]});
    const plugin = await createRam0Hooks({
      environment: {
        RAM0_API_URL: "http://ram0.local:8888",
        RAM0_API_KEY: "ram0-key",
        RAM0_MEMORY_RETRIEVAL: "false",
        RAM0_MEMORY_CAPTURE: "0",
      },
      fetcher: recorded.fetcher,
      dataDir,
      project: "repo-a",
    });

    await plugin["chat.message"]?.({sessionID: "s1"}, userOutput("Decision: Never transmit this."));
    await plugin["experimental.session.compacting"]?.({sessionID: "s1"}, {context: []});
    await plugin.event?.({event: {type: "session.idle", properties: {sessionID: "s1"}} as any});

    expect(recorded.requests.some((request) => request.url.endsWith("/search"))).toBe(false);
    expect(recorded.requests.some((request) => request.url.endsWith("/memories"))).toBe(false);
  });

  test("uses real two-argument hooks and mutates message and compaction outputs", async () => {
    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const recorded = recorder({memories: [
      'Architecture: The wrapper escapes quotes "safely" & consistently.',
    ]});
    const plugin = await hooks(recorded.fetcher, dataDir);

    await plugin["chat.message"]?.(
      {sessionID: "s1"},
      userOutput("Decision: The debugging flow uses postgres authentication."),
    );
    const output = transformed();
    await plugin["experimental.chat.messages.transform"]?.({}, output);
    const injected = String((output.messages[0].parts[0] as any).text);
    expect(injected).toContain("<ram0-memory-context>");
    expect(injected).toContain("&quot;safely&quot;");
    expect(injected).toContain("&amp; consistently");
    expect(injected).not.toContain("\n## injected");

    await assistantOutput(plugin, "Decision: The debugging flow uses postgres authentication.");
    const compact = {context: [] as string[]};
    await plugin["experimental.session.compacting"]?.({sessionID: "s1"}, compact);
    expect(compact.context.join("\n")).toContain("Ram0 memories preserved across compaction");
    const adds = recorded.requests.filter((request) => request.url.endsWith("/memories"));
    expect(adds).toHaveLength(1);
    expect(adds[0].body).toHaveProperty("infer", false);
    expect(JSON.stringify(adds[0].body)).toContain("post-compaction continuation preserves durable state");
    expect((adds[0].body as any).metadata.ram0_auto_context_version).toBe("1");
    expect((adds[0].body as any).metadata.ram0_auto_context_proof).toHaveLength(64);
    expect(JSON.stringify(adds[0].body)).not.toContain("original");
  });

  test("tool output and event payloads follow real contracts and fail open", async () => {
    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const recorded = recorder({memories: ["Troubleshooting: The prior timeout was resolved by retry."]});
    const plugin = await hooks(recorded.fetcher, dataDir);

    await plugin["tool.execute.after"]?.(
      {tool: "bash", sessionID: "s1", callID: "c1", args: {command: "pytest"}},
      {title: "tests", output: "Error: timeout\n".repeat(4), metadata: {}},
    );
    const output = transformed();
    await plugin["experimental.chat.messages.transform"]?.({}, output);
    expect(String((output.messages[0].parts[0] as any).text)).toContain("prior timeout was resolved");

    await assistantOutput(plugin, "Follow-up: The bounded retry remains pending.");
    await plugin.event?.({event: {type: "session.idle", properties: {sessionID: "s1"}} as any});
    expect(recorded.requests.some((request) => request.url.endsWith("/memories"))).toBe(true);
  });

  test("interleaved sessions drain only their own pending context", async () => {
    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/categories")) return new Response(JSON.stringify({saved: [], active: []}), {status: 200});
      if (url.endsWith("/search")) {
        const query = JSON.parse(String(init?.body)).query as string;
        const memory = `Fact: The result is for ${query}.`;
        return new Response(JSON.stringify({results: [{
          memory,
          metadata: {
            ram0_auto_context_version: "1",
            ram0_auto_context_proof: proof("ram0-key", memory),
          },
        }] }), {status: 200});
      }
      return new Response(JSON.stringify({ok: true}), {status: 200});
    };
    const plugin = await hooks(fetcher, dataDir);
    await plugin["chat.message"]?.({sessionID: "session-a"}, userOutput("debug authentication"));
    await plugin["chat.message"]?.({sessionID: "session-b"}, userOutput("postgres schema"));

    const outputB = transformed("session-b");
    await plugin["experimental.chat.messages.transform"]?.({}, outputB);
    expect(String((outputB.messages[0].parts[0] as any).text)).toContain("database");
    expect(String((outputB.messages[0].parts[0] as any).text)).not.toContain("authentication");

    const outputA = transformed("session-a");
    await plugin["experimental.chat.messages.transform"]?.({}, outputA);
    expect(String((outputA.messages[0].parts[0] as any).text)).toContain("authentication");
    expect(String((outputA.messages[0].parts[0] as any).text)).not.toContain("database");
  });

  test("fresh and customized category catalogs preserve active and saved definitions", () => {
    const defaults = [{name: "technology", description: "Legacy default wording"}];
    const custom = [{name: "architecture_decisions", description: "Owner edited wording"}];

    const fresh = mergeCategoryDefinitions({saved: [], active: defaults});
    const customized = mergeCategoryDefinitions({saved: custom, active: defaults});

    expect(fresh[0]).toEqual(defaults[0]);
    expect(customized.slice(0, 2)).toEqual([...custom, ...defaults]);
    expect(customized.filter((item) => item.name === "architecture_decisions")).toHaveLength(1);
  });

  test("onboarding POSTs only missing definitions and never replaces customized catalog state", async () => {
    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const recorded = recorder({
      categories: {
        saved: [{name: "architecture_decisions", description: "Owner edited wording"}],
        active: [{name: "technology", description: "Legacy default wording"}],
      },
    });
    const plugin = await hooks(recorded.fetcher, dataDir);

    await plugin["chat.message"]?.({sessionID: "s1"}, userOutput("debug auth"));

    const creates = recorded.requests.filter((request) => request.url.endsWith("/categories") && request.init.method === "POST");
    expect(creates.length).toBeGreaterThan(0);
    expect(creates.map((request) => request.body)).not.toContainEqual(
      {name: "architecture_decisions", description: "Owner edited wording"},
    );
    expect(recorded.requests.some((request) => request.url.endsWith("/categories") && request.init.method === "PUT")).toBe(false);
  });

  test("category onboarding marker follows owner across projects", async () => {
    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const recorded = recorder({categories: {saved: [], active: []}});
    for (const project of ["repo-a", "repo-b"]) {
      const plugin = await createRam0Hooks({
        environment: {RAM0_API_URL: "http://ram0.local:8888", RAM0_API_KEY: "ram0-key"},
        fetcher: recorded.fetcher,
        dataDir,
        project,
      });
      await plugin["chat.message"]?.({sessionID: project}, userOutput("debug auth"));
    }
    expect(recorded.requests.filter((request) => request.url.endsWith("/categories") && request.init.method === "GET")).toHaveLength(1);
    expect(recorded.requests.filter((request) => request.url.endsWith("/categories") && request.init.method === "POST").length).toBeGreaterThan(0);
  });

  test("two plugin onboarders tolerate duplicate POST races without losing dashboard definitions", async () => {
    const saved = new Map<string, {name: string; description: string}>([
      ["custom", {name: "custom", description: "Dashboard-owned wording"}],
    ]);
    const createCounts = new Map<string, number>();
    const requests: Recorded[] = [];
    const fetcher = async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      requests.push({url, init: init ?? {}, body});
      if (url.endsWith("/categories") && init?.method === "GET") {
        return new Response(JSON.stringify({saved: [...saved.values()], active: [...saved.values()]}), {status: 200});
      }
      if (url.endsWith("/categories") && init?.method === "POST") {
        await new Promise((resolve) => setTimeout(resolve, 1));
        const definition = body as {name: string; description: string};
        if (saved.has(definition.name)) return new Response(JSON.stringify({detail: "duplicate"}), {status: 400});
        saved.set(definition.name, definition);
        createCounts.set(definition.name, (createCounts.get(definition.name) ?? 0) + 1);
        return new Response(JSON.stringify({saved: [...saved.values()]}), {status: 201});
      }
      if (url.endsWith("/search")) return new Response(JSON.stringify({results: []}), {status: 200});
      return new Response(JSON.stringify({ok: true}), {status: 200});
    };
    const plugins = await Promise.all(["one", "two"].map(async (name) => createRam0Hooks({
      environment: {RAM0_API_URL: "http://ram0.local:8888", RAM0_API_KEY: "ram0-key"},
      fetcher,
      dataDir: await mkdtemp(join(tmpdir(), `ram0-opencode-${name}-`)),
      project: name,
    })));

    await Promise.all(plugins.map((plugin, index) =>
      plugin["chat.message"]?.({sessionID: `s${index}`}, userOutput("debug auth"))));

    const expected = mergeCategoryDefinitions({saved: [{name: "custom", description: "Dashboard-owned wording"}]})
      .filter((definition) => definition.name !== "custom").map((definition) => definition.name);
    expect(saved.get("custom")?.description).toBe("Dashboard-owned wording");
    expect([...createCounts.keys()].sort()).toEqual(expected.sort());
    expect(new Set(createCounts.values())).toEqual(new Set([1]));
    expect(requests.some((request) => request.init.method === "PUT")).toBe(false);
  });

  test("selection rejects raw/source/code and high-risk credentials before add", async () => {
    const raw = [
      "Decision: bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
      "Preference: Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
      "Architecture: aws_access_key_id=AKIAIOSFODNN7EXAMPLE",
      "Follow-up: password=correct-horse-battery-staple",
      "Decision: raw prompt: paste everything",
      "Preference: source: /Users/alice/private.ts",
      "Architecture: code: console.log(secret)",
      "Decision: archive this raw transcript: full conversation follows",
      "Preference: remember AKIAIOSFODNN7EXAMPLE for later",
      "Architecture: owner 123e4567-e89b-42d3-a456-426614174000 uses this boundary",
      "Decision: The adapter remains narrow with sk-abcdefghijklmnopqrstuvwxyz012345, m0sk_abcdefghijklmnopqrstuvwxyz012345, and ram0-key.",
    ].join("\n");

    expect(durableCandidates(raw)).toEqual([
      {kind: "decision", text: "The adapter remains narrow with [redacted credential], [redacted credential], and ram0-key."},
    ]);
    expect(safeRetrievalQuery(`${raw} debug postgres auth`)).toBe(
      "Relevant durable coding context: architecture, authentication, database, debugging",
    );

    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const recorded = recorder();
    const plugin = await hooks(recorded.fetcher, dataDir);
    await assistantOutput(plugin, raw, "private");
    await plugin.event?.({event: {type: "session.idle", properties: {sessionID: "private"}} as any});
    const payload = JSON.stringify(
      recorded.requests.filter((request) => request.url.endsWith("/memories")).map((request) => request.body),
    );
    expect(payload).toContain("adapter remains narrow");
    for (const forbidden of ["eyJ", "Bearer abc", "AKIA", "correct-horse", "raw prompt", "raw transcript", "/Users", "console.log", "123e4567", "m0sk_", "ram0-key"]) {
      expect(payload).not.toContain(forbidden);
    }
  });

  test("automatic retrieval follows the shared secret and instruction policy", async () => {
    const configuredKey = "local-key-with-unusual-format";
    const inject = async (memories: string[]) => {
      const recorded = recorder({memories, memoryKey: configuredKey});
      const plugin = await createRam0Hooks({
        environment: {RAM0_API_URL: "http://ram0.local:8888", RAM0_API_KEY: configuredKey},
        fetcher: recorded.fetcher,
        dataDir: await mkdtemp(join(tmpdir(), "ram0-opencode-")),
        project: "repo-a",
      });
      await plugin["chat.message"]?.({sessionID: "s1"}, userOutput("architecture"));
      const output = transformed();
      await plugin["experimental.chat.messages.transform"]?.({}, output);
      return String((output.messages[0].parts[0] as any).text);
    };

    const acceptedContext = await inject(AUTOMATIC_CONTEXT_POLICY.accepted);
    for (const accepted of AUTOMATIC_CONTEXT_POLICY.accepted) expect(acceptedContext).toContain(accepted);
    for (const rejected of AUTOMATIC_CONTEXT_POLICY.rejected) {
      expect(await inject([rejected])).toBe("original");
    }
    const configuredContext = await inject([
      AUTOMATIC_CONTEXT_POLICY.configured_key_template.replace("{key}", configuredKey),
    ]);
    expect(configuredContext).not.toContain(configuredKey);
  });

  test("unsigned memories remain explicit-search results but never automatic context", async () => {
    const memory = "Fact: The direct result remains explicitly readable.";
    const recorded = recorder({memories: [memory], trusted: false});
    const plugin = await hooks(recorded.fetcher, await mkdtemp(join(tmpdir(), "ram0-opencode-")));
    await plugin["chat.message"]?.({sessionID: "s1"}, userOutput("architecture"));
    const output = transformed();
    await plugin["experimental.chat.messages.transform"]?.({}, output);
    expect(String((output.messages[0].parts[0] as any).text)).toBe("original");

    const client = new Ram0Client(
      {apiUrl: "http://ram0.local:8888", apiKey: "ram0-key"},
      recorded.fetcher,
    );
    expect(await client.search("architecture", 5)).toEqual({results: [{id: "0", memory}]});
  });

  test("user chat can retrieve context but never becomes signed capture provenance", async () => {
    const recorded = recorder({memories: AUTOMATIC_CONTEXT_POLICY.accepted});
    const plugin = await hooks(recorded.fetcher, await mkdtemp(join(tmpdir(), "ram0-opencode-")));
    await plugin["chat.message"]?.(
      {sessionID: "s1"},
      userOutput("Fact: The user payload remains deceptively declarative."),
    );
    await plugin.event?.({event: {type: "session.idle", properties: {sessionID: "s1"}} as any});
    expect(recorded.requests.filter((request) => request.url.endsWith("/memories"))).toHaveLength(0);
  });

  test("dedup persists across plugin reloads and is endpoint-owner-project scoped", async () => {
    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const recorded = recorder();
    for (let index = 0; index < 2; index += 1) {
      const plugin = await hooks(recorded.fetcher, dataDir);
      await assistantOutput(plugin, "Decision: The durable fact remains persistent.");
      await plugin.event?.({event: {type: "session.idle", properties: {sessionID: "s1"}} as any});
    }
    expect(recorded.requests.filter((request) => request.url.endsWith("/memories"))).toHaveLength(1);
  });

  test("HTTP timeout aborts and hook remains fail open", async () => {
    const client = new Ram0Client(
      {apiUrl: "http://ram0.local:8888", apiKey: "ram0-key"},
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
      10,
    );
    await expect(client.search("safe", 3)).rejects.toThrow("network_error");

    const dataDir = await mkdtemp(join(tmpdir(), "ram0-opencode-"));
    const plugin = await createRam0Hooks({
      environment: {RAM0_API_URL: "http://ram0.local:8888", RAM0_API_KEY: "ram0-key"},
      fetcher: (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
      timeoutMs: 10,
      dataDir,
      project: "repo-a",
    });
    await expect(plugin["chat.message"]?.({sessionID: "s1"}, userOutput("debug auth"))).resolves.toBeUndefined();
  });
});
