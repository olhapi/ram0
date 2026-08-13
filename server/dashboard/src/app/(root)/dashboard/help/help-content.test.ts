import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  agentInstalls,
  mcpUrl,
  persistentSetupCommand,
  skillInstallCommand,
} from "./help-content.ts";

test("URL helpers validate and normalize the configured endpoint", () => {
  assert.equal(
    mcpUrl("https://api.example.test/"),
    "https://api.example.test/mcp",
  );
  assert.equal(
    persistentSetupCommand("https://api.example.test/"),
    "ram0 setup --url 'https://api.example.test'",
  );
  for (const invalid of [
    undefined,
    "ftp://host",
    "https://u:p@host",
    "https://host/?key=x",
    "https://host/$(bad)",
  ]) {
    assert.equal(mcpUrl(invalid), null);
    assert.deepEqual(agentInstalls(invalid), []);
  }
});

test("Help gives supported clients one public, secret-free setup flow", () => {
  const installs = agentInstalls("https://api.example.test");
  assert.deepEqual(
    installs.map(({ id }) => id),
    ["codex", "claude-code"],
  );
  for (const install of installs) {
    const generated = JSON.stringify(install);
    assert.equal(
      install.persistentSetup,
      "ram0 setup --url 'https://api.example.test'",
    );
    assert.equal(install.configVerify, "ram0 config test");
    assert.match(install.pluginNote, /restart|reload/i);
    assert.match(
      install.pluginInstall,
      /https:\/\/github\.com\/olhapi\/ram0-plugins\.git/,
    );
    assert.match(install.pluginUpdate, /ram0-plugins/);
    assert.match(install.migration, /mem0-plugins/);
    assert.match(install.troubleshooting.join("\n"), /ram0 config set-key/);
    assert.match(
      install.troubleshooting.join("\n"),
      /chmod 600 ~\/\.config\/ram0\/config\.json/,
    );
    assert.match(install.troubleshooting.join("\n"), /missing configuration/i);
    assert.match(install.troubleshooting.join("\n"), /unreachable endpoint/i);
    assert.doesNotMatch(
      generated,
      /git clone|~\/ram0-plugins|contribut|bun install|file:\/\/|export RAM0_API_(?:URL|KEY)|launchctl|Authorization.*Bearer|m0sk_/i,
    );
  }
});

test("marketplace installs use the Ram0-only identity", () => {
  const installs = agentInstalls("https://api.example.test");
  for (const install of installs.filter(
    ({ id }) => id === "codex" || id === "claude-code",
  )) {
    assert.match(install.pluginInstall, /ram0@ram0-plugins/);
    assert.doesNotMatch(install.pluginInstall, /ram0@mem0-plugins/);
  }
});

test("Help page renders permanent setup, migration, and troubleshooting", () => {
  const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
  for (const text of [
    "Connect Agents",
    "Permanent Ram0 setup",
    "Migration",
    "Troubleshooting",
    "Using Ram0",
  ]) {
    assert.match(page, new RegExp(text));
  }
  assert.match(page, /install\.pluginInstall/);
  assert.match(page, /install\.pluginUpdate/);
  assert.match(page, /install\.persistentSetup/);
  assert.match(page, /install\.configVerify/);
  assert.match(page, /~\/\.config\/ram0\/config\.json/);
  assert.doesNotMatch(
    page,
    /git clone|~\/ram0-plugins|contribut|source build|protected environment|launchctl|Authorization: Bearer/i,
  );
  assert.match(page, />\s*Mem0 MCP guide\s*</);
  assert.doesNotMatch(
    page,
    /href="https:\/\/docs\.mem0\.ai[^\"]*"[\s\S]{0,120}>\s*Ram0/i,
  );
});

test("skill-only installation remains available", () => {
  assert.equal(
    skillInstallCommand,
    "npx skills add https://github.com/olhapi/ram0 --skill ram0-memory",
  );
});
