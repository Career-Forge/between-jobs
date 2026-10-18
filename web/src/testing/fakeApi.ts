// A stand-in for the API client (lib/api.ts) for component tests: the test says what each request
// answers, and every request that was made is recorded, body included. Mock the client with it:
//
//   vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);
//
// A request nobody described fails loudly, so a component that starts asking for something new does
// not pass by getting `undefined` back.

export class FakeApiError extends Error {
  constructor(
    message: string,
    public readonly code?: string,
    public readonly reason?: string,
    public readonly status = 400,
    public readonly retryable?: boolean,
  ) {
    super(message);
  }
}

export interface RecordedCall {
  method: string;
  path: string;
  body: unknown;
}

type Answer = unknown | Error | ((call: RecordedCall) => unknown);

const routes = new Map<string, Answer>();
export const calls: RecordedCall[] = [];

export function reset(): void {
  routes.clear();
  calls.length = 0;
}

// Describe a route as "GET /credentials". An Error rejects, a function is called with the request
// (and may return a value, or throw), anything else is the answer.
export function on(route: string, answer: Answer): void {
  routes.set(route, answer);
}

export function callsTo(route: string): RecordedCall[] {
  return calls.filter((call) => `${call.method} ${call.path}` === route);
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  let body: unknown = undefined;
  if (typeof init?.body === "string") body = JSON.parse(init.body);
  const call: RecordedCall = { method, path, body };
  calls.push(call);
  const answer = routes.get(`${method} ${path}`);
  if (answer === undefined && !routes.has(`${method} ${path}`)) {
    throw new Error(`fakeApi: nothing described for ${method} ${path}`);
  }
  const value = typeof answer === "function" ? (answer as (call: RecordedCall) => unknown)(call) : answer;
  if (value instanceof Error) throw value;
  return value as T;
}

// What `vi.mock` returns for lib/api.
export const apiModule = { apiFetch, ApiError: FakeApiError };
