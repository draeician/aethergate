import { describe, expect, it } from "vitest";
import { hasRole, isSystemAdmin, canCancel } from "./roles";

describe("role helpers", () => {
  it("detects system_admin", () => {
    expect(isSystemAdmin(["system_admin"])).toBe(true);
    expect(isSystemAdmin(["project_admin"])).toBe(false);
  });

  it("grants cancel to system_admin and project_admin only", () => {
    expect(canCancel(["system_admin"])).toBe(true);
    expect(canCancel(["project_admin"])).toBe(true);
    expect(canCancel(["project_viewer"])).toBe(false);
  });

  it("checks role membership", () => {
    expect(hasRole(["project_viewer"], "project_viewer")).toBe(true);
    expect(hasRole(["project_viewer"], "project_admin")).toBe(false);
  });
});
