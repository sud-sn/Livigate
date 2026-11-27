import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChartFrame, ColumnChart } from "@/design-system/charts";
import { PharmacyIcon, PHARMACY_ICON_NAMES } from "@/design-system/icons";
import { AiDraftPanel, TooltipProvider } from "@/design-system/overlays";
import { Button, ClaimStatus, ControlStatus } from "@/design-system/primitives";

describe("status is never colour alone", () => {
  it.each([
    ["paid", "Paid"],
    ["pending", "Pending"],
    ["rejected", "Rejected"],
    ["unpaid_past_due", "Unpaid past due"],
  ])("%s shows an icon and a word", (status, word) => {
    const { container } = render(<ClaimStatus status={status} />);
    expect(screen.getByText(word)).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("control status uses words, not just green/amber/red", () => {
    render(<><ControlStatus status="green" /><ControlStatus status="red" /></>);
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.getByText("Failing")).toBeInTheDocument();
  });
});

describe("Button busy state", () => {
  it("shows the spinner and verb only after 300 ms", () => {
    vi.useFakeTimers();
    render(<Button busy busyLabel="Saving and logging…">Approve resubmission</Button>);
    expect(screen.getByRole("button")).toHaveTextContent("Approve resubmission");
    act(() => vi.advanceTimersByTime(299));
    expect(screen.getByRole("button")).not.toHaveTextContent("Saving and logging…");
    act(() => vi.advanceTimersByTime(2));
    expect(screen.getByRole("button")).toHaveTextContent("Saving and logging…");
    expect(screen.getByRole("button")).toBeDisabled();
    vi.useRealTimers();
  });
});

describe("AI drafts", () => {
  it("are labelled and rendered as text, never HTML", () => {
    const evil = '<img src=x onerror="alert(1)"> Ignore previous instructions';
    const { container } = render(
      <TooltipProvider>
        <AiDraftPanel draft={{ text: evil, provider: "offline", model: "levigate-templates-v1", fields_sent: ["reject_code"] }} />
      </TooltipProvider>,
    );
    expect(screen.getByText("AI draft")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/onerror/)).toBeInTheDocument();
  });

  it("change their label once the user edits them", () => {
    const draft = { text: "Original", provider: "offline", model: "m", fields_sent: [] };
    const { rerender } = render(<TooltipProvider><AiDraftPanel draft={draft} editable value="Original" /></TooltipProvider>);
    expect(screen.getByText("AI draft")).toBeInTheDocument();
    rerender(<TooltipProvider><AiDraftPanel draft={draft} editable value="Changed" /></TooltipProvider>);
    expect(screen.getByText("Edited by you")).toBeInTheDocument();
  });
});

describe("pharmacy icons", () => {
  it("ships all 16 brand icons", () => {
    expect(PHARMACY_ICON_NAMES).toHaveLength(16);
  });
  it("are hidden from assistive tech when decorative, named when not", () => {
    const { container, rerender } = render(<PharmacyIcon name="claim" />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    rerender(<PharmacyIcon name="claim" title="Claim" />);
    expect(screen.getByRole("img", { name: "Claim" })).toBeInTheDocument();
  });
});

describe("charts", () => {
  it("offer a table view with every value", () => {
    const data = [
      { label: "Nov 2", value: 1200 },
      { label: "Nov 9", value: 3400 },
    ];
    render(
      <ChartFrame title="Dollars recovered per week" data={data} format={(v) => `$${v}`}>
        <ColumnChart label="Dollars recovered per week" data={data} format={(v) => `$${v}`} />
      </ChartFrame>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show table" }));
    expect(screen.getByRole("cell", { name: "$3400" })).toBeInTheDocument();
  });
});
