import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { createNoteScope } from "./noteScope";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("createNoteScope", () => {
  it("routes standalone and workspace thread listing through matching APIs", async () => {
    const standaloneList = vi.spyOn(api, "listStandaloneNoteThreads").mockResolvedValue([]);
    const workspaceList = vi.spyOn(api, "listNoteThreads").mockResolvedValue([]);

    const standalone = createNoteScope({});
    const workspace = createNoteScope({ workspaceId: "project-1" });
    await standalone.listThreads();
    await workspace.listThreads();

    expect(standalone.kind).toBe("standalone");
    expect(standalone.id).toBe("standalone");
    expect(workspace.kind).toBe("workspace");
    expect(workspace.id).toBe("project-1");
    expect(standaloneList).toHaveBeenCalledOnce();
    expect(workspaceList).toHaveBeenCalledWith("project-1");
  });

  it("keeps stream turns on the shared thread-stream API", async () => {
    const stream = vi.spyOn(api, "streamNoteThreadTurn").mockResolvedValue(undefined);
    const onEvent = vi.fn();
    const scope = createNoteScope({ workspaceId: "project-2" });
    const data = { message: "continue", auto_execute: true, idempotency_key: "turn-1" };

    await scope.streamTurn("thread-1", data, onEvent);

    expect(stream).toHaveBeenCalledWith("thread-1", data, onEvent, undefined);
  });
});
