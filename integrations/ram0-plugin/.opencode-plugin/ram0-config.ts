import {lstat, readFile} from "node:fs/promises";
import {homedir} from "node:os";
import {join} from "node:path";

import type {Environment} from "./ram0-client.ts";

const DEFAULT_API_URL = "http://localhost:8888";

export type Ram0Config = {
  apiUrl: string;
  apiKey: string;
  retrievalEnabled: boolean;
  captureEnabled: boolean;
};

export type Ram0ConfigOptions = {home?: string};

function booleanSetting(value: string | undefined, fallback: boolean): boolean {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return fallback;
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  throw new Error("Ram0 retrieval and capture settings must be boolean values.");
}

export function normalizeApiUrl(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    throw new Error("RAM0 API URL must be an absolute HTTP(S) URL without credentials, query, or fragment.");
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error("RAM0 API URL must be an absolute HTTP(S) URL without credentials, query, or fragment.");
  }
  return parsed.toString().replace(/\/+$/, "");
}

async function storedConfig(home: string): Promise<Record<string, unknown>> {
  const directory = join(home, ".config", "ram0");
  const path = join(directory, "config.json");
  try {
    const directoryInfo = await lstat(directory);
    if (!directoryInfo.isDirectory() || directoryInfo.isSymbolicLink()) {
      throw new Error(`Ram0 config directory must be a regular directory: ${directory}`);
    }
    if (process.platform !== "win32" && (directoryInfo.mode & 0o077) !== 0) {
      throw new Error(`Ram0 config directory permissions are unsafe; run \`chmod 700 ${directory}\`.`);
    }
    const fileInfo = await lstat(path);
    if (!fileInfo.isFile() || fileInfo.isSymbolicLink()) {
      throw new Error(`Ram0 config must be a regular file: ${path}`);
    }
    if (process.platform !== "win32" && (fileInfo.mode & 0o077) !== 0) {
      throw new Error(`Ram0 config permissions are unsafe; run \`chmod 600 ${path}\`.`);
    }
    const value = JSON.parse(await readFile(path, "utf8")) as unknown;
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error(`Ram0 config must contain a JSON object: ${path}`);
    }
    return value as Record<string, unknown>;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw error;
  }
}

export async function loadRam0Config(
  environment: Environment,
  options: Ram0ConfigOptions = {},
): Promise<Ram0Config> {
  const stored = await storedConfig(options.home ?? homedir());
  const rawUrl = environment.RAM0_API_URL || stored.api_url || DEFAULT_API_URL;
  const rawKey = environment.RAM0_API_KEY || stored.api_key || "";
  if (typeof rawUrl !== "string") throw new Error("Ram0 config api_url must be a string.");
  if (typeof rawKey !== "string") throw new Error("Ram0 config api_key must be a string.");
  const apiKey = rawKey.trim();
  if (!apiKey) throw new Error("Ram0 API key is missing; run `ram0 setup`.");
  return {
    apiUrl: normalizeApiUrl(rawUrl),
    apiKey,
    retrievalEnabled: booleanSetting(environment.RAM0_MEMORY_RETRIEVAL, true),
    captureEnabled: booleanSetting(environment.RAM0_MEMORY_CAPTURE, true),
  };
}
