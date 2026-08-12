import {describe, expect, test} from "bun:test";
import {chmod, mkdir, mkdtemp, readFile, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";

import {loadRam0Config, normalizeApiUrl} from "./ram0-config.ts";

type Contract = {valid_urls: Array<[string, string]>; invalid_urls: string[]};
const contract = JSON.parse(
  await readFile(new URL("../tests/config_contract.json", import.meta.url), "utf8"),
) as Contract;

async function fixture(apiUrl = "https://brain-api.olhapi.com", apiKey = "stored-key") {
  const home = await mkdtemp(join(tmpdir(), "ram0-config-"));
  const directory = join(home, ".config", "ram0");
  await mkdir(directory, {recursive: true, mode: 0o700});
  await chmod(directory, 0o700);
  const path = join(directory, "config.json");
  await writeFile(path, JSON.stringify({api_url: apiUrl, api_key: apiKey}), {mode: 0o600});
  await chmod(path, 0o600);
  return {home, directory, path};
}

describe("persistent Ram0 configuration", () => {
  test("loads file values with an empty process environment", async () => {
    const {home} = await fixture();
    const config = await loadRam0Config({}, {home});
    expect(config).toEqual({
      apiUrl: "https://brain-api.olhapi.com",
      apiKey: "stored-key",
      retrievalEnabled: true,
      captureEnabled: true,
    });
  });

  test("environment overrides only the explicitly supplied field", async () => {
    const {home} = await fixture();
    expect((await loadRam0Config({RAM0_API_URL: "https://env.example"}, {home})).apiKey).toBe("stored-key");
    expect((await loadRam0Config({RAM0_API_KEY: "env-key"}, {home})).apiUrl).toBe(
      "https://brain-api.olhapi.com",
    );
  });

  test("shares literal URL validation cases with the Python loader", () => {
    for (const [raw, expected] of contract.valid_urls) expect(normalizeApiUrl(raw)).toBe(expected);
    for (const raw of contract.invalid_urls) expect(() => normalizeApiUrl(raw)).toThrow("absolute HTTP");
  });

  test("rejects unsafe file and directory permissions without disclosing the key", async () => {
    const {home, directory, path} = await fixture();
    await chmod(path, 0o644);
    await expect(loadRam0Config({}, {home})).rejects.toThrow("chmod 600");
    await chmod(path, 0o600);
    await chmod(directory, 0o755);
    try {
      await loadRam0Config({}, {home});
      throw new Error("expected permission rejection");
    } catch (error) {
      expect(String(error)).toContain("chmod 700");
      expect(String(error)).not.toContain("stored-key");
    }
  });

  test("requires a non-empty key but keeps the localhost URL default", async () => {
    const home = await mkdtemp(join(tmpdir(), "ram0-config-empty-"));
    await expect(loadRam0Config({}, {home})).rejects.toThrow("ram0 setup");
    const config = await loadRam0Config({RAM0_API_KEY: "env-key"}, {home});
    expect(config.apiUrl).toBe("http://localhost:8888");
  });
});
