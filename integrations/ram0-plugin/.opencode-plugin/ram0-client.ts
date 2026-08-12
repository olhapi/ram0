export type Environment = Record<string, string | undefined>;
export type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;
export type Ram0Connection = {apiUrl: string; apiKey: string};
const DEFAULT_TIMEOUT_MS = 10_000;

export class Ram0ClientError extends Error {
  constructor(readonly code: string) {
    super(`Ram0 request failed (${code})`);
  }
}

export class Ram0Client {
  private readonly apiUrl: string;
  private readonly apiKey: string;
  private readonly fetcher: Fetcher;
  private readonly timeoutMs: number;

  constructor(connection: Ram0Connection, fetcher: Fetcher = fetch, timeoutMs = DEFAULT_TIMEOUT_MS) {
    const parsed = new URL(connection.apiUrl);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") throw new Error("invalid Ram0 URL");
    this.apiUrl = connection.apiUrl.replace(/\/+$/, "");
    this.apiKey = connection.apiKey.trim();
    if (!this.apiKey) throw new Error("Ram0 API key is required; run `ram0 setup`.");
    this.fetcher = fetcher;
    this.timeoutMs = timeoutMs;
  }

  async search(query: string, limit = 5): Promise<unknown> {
    return this.request("/search", {query, top_k: limit});
  }

  async add(memory: string, metadata: Record<string, string>): Promise<unknown> {
    return this.request("/memories", {messages: [{role: "user", content: memory}], metadata});
  }

  async addDurable(memory: string, metadata: Record<string, string>): Promise<unknown> {
    return this.request("/memories", {messages: [{role: "user", content: memory}], metadata, infer: false});
  }

  async getCategories(): Promise<unknown> {
    return this.request("/categories");
  }

  async createCategory(definition: Record<string, string>): Promise<unknown> {
    return this.request("/categories", definition);
  }

  async putCategories(definitions: Array<Record<string, string>>): Promise<unknown> {
    return this.request("/categories", definitions, "PUT");
  }

  private async request(path: string, body?: unknown, method = "POST"): Promise<unknown> {
    const headers: Record<string, string> = {Authorization: `Bearer ${this.apiKey}`};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetcher(`${this.apiUrl}${path}`, {
        method: body === undefined ? "GET" : method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        redirect: "error",
      });
      if (!response.ok) throw new Ram0ClientError(`http_${response.status}`);
      try {
        return await response.json();
      } catch {
        throw new Ram0ClientError("invalid_response");
      }
    } catch (error) {
      if (error instanceof Ram0ClientError) throw error;
      throw new Ram0ClientError("network_error");
    } finally {
      clearTimeout(timeout);
    }
  }
}
