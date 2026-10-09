import { describe, expect, it } from "vitest";
import openapi from "./openapi.json";

const MANAGEMENT_PATHS = [
  "/admin/v1/projects",
  "/admin/v1/role-assignments",
  "/admin/v1/credentials",
  "/admin/v1/providers",
  "/admin/v1/provider-accounts",
  "/admin/v1/secret-refs",
  "/admin/v1/endpoints",
  "/admin/v1/quota-groups",
  "/admin/v1/quota-limits",
  "/admin/v1/model-aliases",
  "/admin/v1/route-bindings",
];

const MANAGEMENT_TYPES = [
  "EndpointRead",
  "ProviderRead",
  "ProviderAccountRead",
  "SecretRefRead",
  "RouteBindingRead",
  "ModelAliasRead",
  "QuotaGroupRead",
  "QuotaLimitRead",
  "ApiCredentialRead",
  "PrincipalRead",
  "RoleAssignmentRead",
];

describe("generated OpenAPI client", () => {
  it("covers the management endpoints exposed by the backend", () => {
    for (const path of MANAGEMENT_PATHS) {
      expect(openapi.paths, `missing path ${path}`).toHaveProperty(path);
    }
  });

  it("defines the generated management types consumed by the client", () => {
    for (const type of MANAGEMENT_TYPES) {
      expect(openapi.components.schemas, `missing type ${type}`).toHaveProperty(type);
    }
  });
});
