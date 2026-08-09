import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InlineAiWidget } from "./InlineAiWidget";

const baseProps = {
  top: 20,
  left: 40,
  onAccept: vi.fn(),
  onReject: vi.fn(),
  onClose: vi.fn(),
  isGenerating: false,
  hasGenerated: false,
};

describe("InlineAiWidget", () => {
  it("submits a trimmed inline edit prompt", () => {
    const onGenerate = vi.fn();

    render(<InlineAiWidget {...baseProps} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByPlaceholderText("Describe code edit (e.g. convert theme, fix syntax, add column)..."), { target: { value: "  add a title  " } });
    fireEvent.click(screen.getByRole("button"));

    expect(onGenerate).toHaveBeenCalledWith("add a title");
  });

  it("closes on Escape and accepts a generated diff with the keyboard shortcut", () => {
    const onClose = vi.fn();
    const onAccept = vi.fn();

    const { container, rerender } = render(
      <InlineAiWidget {...baseProps} onClose={onClose} onAccept={onAccept} onGenerate={vi.fn()} />,
    );

    fireEvent.keyDown(screen.getByPlaceholderText("Describe code edit (e.g. convert theme, fix syntax, add column)..."), { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();

    rerender(
      <InlineAiWidget
        {...baseProps}
        hasGenerated
        onClose={onClose}
        onAccept={onAccept}
        onGenerate={vi.fn()}
      />,
    );
    fireEvent.keyDown(container.firstElementChild as HTMLElement, {
      key: "Enter",
      ctrlKey: true,
    });

    expect(onAccept).toHaveBeenCalledOnce();
  });
});
