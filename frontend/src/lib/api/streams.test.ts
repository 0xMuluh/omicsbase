import { afterEach, describe, expect, it, vi } from "vitest";

import { streamNdjsonWithReplay } from "./streams";

function responseFromChunks(chunks: string[]): Response {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "application/x-ndjson" } },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("streamNdjsonWithReplay", () => {
  it("deduplicates replayed events after an interrupted response", async () => {
    const first = JSON.stringify({ type: "progress", sequence: 1, message: "first" });
    const replay = JSON.stringify({ type: "progress", sequence: 1, message: "first" });
    const terminal = JSON.stringify({ type: "run", sequence: 2, run: { status: "completed" } });
    const fetchMock = vi.fn();
    fetchMock
      .mockResolvedValueOnce(responseFromChunks([first.slice(0, 10), first.slice(10) + "\n"]))
      .mockResolvedValueOnce(responseFromChunks([replay + "\n", terminal + "\n"]));
    vi.stubGlobal("fetch", fetchMock);

    const events: Array<{ type?: string; sequence?: number }> = [];
    await streamNdjsonWithReplay(
      "/projects/p/agent/stream",
      { message: "hello", idempotency_key: "fixed-key" },
      { "Content-Type": "application/json" },
      (event) => events.push(event),
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(events.map((event) => event.type)).toEqual(["progress", "run"]);
    const firstRequest = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    const replayRequest = JSON.parse(String(fetchMock.mock.calls[1][1].body));
    expect(firstRequest.idempotency_key).toBe("fixed-key");
    expect(replayRequest.idempotency_key).toBe("fixed-key");
  });

  it("stops when a queued run reaches completed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseFromChunks([
        JSON.stringify({ type: "run", sequence: 1, run: { status: "queued" } }) + "\n",
        JSON.stringify({ type: "run", sequence: 2, run: { status: "completed" } }) + "\n",
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const events: Array<{ type?: string; sequence?: number }> = [];
    await streamNdjsonWithReplay(
      "/notes/thread/turn",
      { message: "run this" },
      {},
      (event) => events.push(event),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(events.map((event) => event.sequence)).toEqual([1, 2]);
  });
});
