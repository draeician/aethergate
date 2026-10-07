import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import LoginPage from "./LoginPage";

describe("LoginPage", () => {
  it("renders an OIDC sign-in link and no credential input", () => {
    render(<LoginPage />);
    const link = screen.getByRole("link", { name: /sign in with oidc/i });
    expect(link).toHaveAttribute("href", "/admin/v1/auth/oidc/login");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("password")).toBeNull();
    expect(screen.queryByLabelText(/admin key/i)).toBeNull();
  });
});
