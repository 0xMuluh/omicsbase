import { afterEach, describe, expect, it, vi } from "vitest";

import { request } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("request", () => {
  it("preserves the conflict status in API errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("file changed", { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(request("/projects/p/files/content/report.html")).rejects.toThrow(
      "API error 409: file changed",
    );
    const options = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(options.headers);
    expect(headers.get("If-Match")).toBeNull();
    expect(headers.get("X-Tenant-ID")).toBe("default_tenant");
    expect(headers.get("X-User-ID")).toBe("default_user");
  });
});
