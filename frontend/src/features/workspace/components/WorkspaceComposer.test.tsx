import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceComposer } from "./WorkspaceComposer";

describe("WorkspaceComposer", () => {
  it("answers a pending agent question from an option", () => {
    const onAnswer = vi.fn();

    render(
      <WorkspaceComposer
        pendingQuestion={{
          id: "question-1",
          question: "Which grouping should I use?",
          options: ["Treatment", "Timepoint"],
          multiple: false,
        }}
        chatMode="build"
        disabled={false}
        onSend={vi.fn()}
        onAnswer={onAnswer}
        onModeChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Treatment" }));

    expect(onAnswer).toHaveBeenCalledWith("Treatment");
  });

  it("stages a file, includes it on submit, and clears the staged chip", () => {
    const onSend = vi.fn();
    const file = new File(["sample"], "counts.csv", { type: "text/csv" });

    const { container } = render(
      <WorkspaceComposer
        pendingQuestion={null}
        chatMode="discuss"
        disabled={false}
        onSend={onSend}
        onAnswer={vi.fn()}
        onModeChange={vi.fn()}
      />,
    );

    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });

    expect(screen.getByText("counts.csv")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Discuss methods or plan a change..."), { target: { value: "Compare the groups" } });
    fireEvent.click(screen.getByTitle("Send"));

    expect(onSend).toHaveBeenCalledWith("Compare the groups", "discuss", [file]);
    expect(screen.queryByText("counts.csv")).not.toBeInTheDocument();
  });
});
