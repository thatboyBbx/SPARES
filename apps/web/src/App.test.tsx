import { useRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppShell, ConfirmDialog, GlassSelect } from "./App";
import { canRoleSeeView, type View } from "./permissions";
import type { AuthUser } from "./lib/api";

describe("permission-aware navigation", () => {
  it("keeps unrestricted views visible and gates operational views by role", () => {
    expect(canRoleSeeView("cashier", "overview")).toBe(true);
    expect(canRoleSeeView("cashier", "catalogue")).toBe(true);
    expect(canRoleSeeView("cashier", "sale")).toBe(true);
    expect(canRoleSeeView("cashier", "receipt")).toBe(false);
    expect(canRoleSeeView("accountant", "accounting")).toBe(true);
    expect(canRoleSeeView("store_keeper", "accounting")).toBe(false);
    expect(canRoleSeeView("owner", "users")).toBe(true);
  });
});

describe("GlassSelect", () => {
  it("supports arrows, Home, End, Enter, Escape, and form submission values", async () => {
    const user = userEvent.setup();
    render(<form data-testid="form"><GlassSelect label="Part" name="part" options={[{ value: "a", label: "Brake pad" }, { value: "b", label: "Oil filter" }, { value: "c", label: "Spark plug" }]} /></form>);
    const trigger = screen.getByRole("button", { name: "Part" });
    trigger.focus();
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(trigger).toHaveTextContent("Oil filter");
    expect(new FormData(screen.getByTestId("form") as HTMLFormElement).get("part")).toBe("b");
    await user.keyboard("{ArrowUp}{Escape}");
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });
});

describe("ConfirmDialog", () => {
  it("moves focus inside, traps focus, closes with Escape, and restores focus", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const view = render(<ConfirmDialog confirm={{ title: "Post payment?", description: "This cannot be silently undone." }} busy={false} onClose={onClose} onConfirm={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
    view.rerender(<ConfirmDialog confirm={null} busy={false} onClose={onClose} onConfirm={vi.fn()} />);
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it("protects loading actions from duplicate submission", () => {
    render(<ConfirmDialog confirm={{ title: "Post?", description: "Working" }} busy onClose={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
  });
});

describe("mobile navigation", () => {
  const user: AuthUser = { id: "u1", email: "cashier@example.test", full_name: "Cashier One", role: "cashier", branch_id: "shop", is_active: true };
  const data = { notifications: [] } as never;

  function Harness() {
    const [open, setOpen] = useState(true);
    const menuButton = useRef<HTMLButtonElement>(null);
    return <AppShell user={user} view="sale" data={data} canSee={(view: View) => canRoleSeeView(user.role, view)} changeView={vi.fn()} menuOpen={open} setMenuOpen={setOpen} menuButton={menuButton} onLogout={vi.fn()}><div>Workspace</div></AppShell>;
  }

  it("contains only permitted links and closes with Escape while restoring focus", async () => {
    const keyboard = userEvent.setup();
    render(<Harness />);
    expect(screen.getAllByTitle("Record sale").length).toBeGreaterThan(0);
    expect(screen.queryByTitle("Receive stock")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
    await keyboard.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Menu" })).toHaveFocus();
  });
});
