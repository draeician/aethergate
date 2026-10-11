import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProvidersPage from "./ProvidersPage";
import { api } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

describe("ProvidersPage", () => {
  it("creates a provider with kind, name and capabilities", async () => {
    vi.spyOn(api, "listProviders").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    const createProvider = vi.spyOn(api, "createProvider").mockResolvedValue({
      id: "prov1",
      kind: "openai",
      name: "OpenAI",
      capabilities: ["text"],
      is_active: true,
    });

    render(<ProvidersPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New provider" }));
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "openai" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "OpenAI" } });
    fireEvent.click(screen.getByLabelText("text"));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createProvider).toHaveBeenCalledWith({
        kind: "openai",
        name: "OpenAI",
        capabilities: ["text"],
        is_active: true,
      }),
    );
  });

  it("creates a provider advertising image capability", async () => {
    vi.spyOn(api, "listProviders").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    const createProvider = vi.spyOn(api, "createProvider").mockResolvedValue({
      id: "prov2",
      kind: "ollama",
      name: "Ollama",
      capabilities: ["text", "image"],
      is_active: true,
    });

    render(<ProvidersPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New provider" }));
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "ollama" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ollama" } });
    fireEvent.click(screen.getByLabelText("text"));
    fireEvent.click(screen.getByLabelText("image"));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createProvider).toHaveBeenCalledWith({
        kind: "ollama",
        name: "Ollama",
        capabilities: ["text", "image"],
        is_active: true,
      }),
    );
  });
});
