import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ModelsPage from "./ModelsPage";
import { api } from "../../lib/client";
import { wrap, systemAdminSession } from "../../test/helpers";

describe("ModelsPage", () => {
  it("creates a model alias with a default text capability", async () => {
    vi.spyOn(api, "listModelAliases").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    const createModelAlias = vi.spyOn(api, "createModelAlias").mockResolvedValue({
      id: "m1",
      name: "gpt-4",
      capabilities: ["text"],
      is_active: true,
    });

    render(<ModelsPage />, { wrapper: wrap({ session: systemAdminSession(), roles: ["system_admin"] }) });

    fireEvent.click(screen.getByRole("button", { name: "New alias" }));
    fireEvent.change(screen.getByLabelText("Alias name"), { target: { value: "gpt-4" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(createModelAlias).toHaveBeenCalledWith({
        name: "gpt-4",
        capabilities: ["text"],
        is_active: true,
      }),
    );
  });
});
