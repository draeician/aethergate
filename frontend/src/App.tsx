import { BrowserRouter, Routes, Route, Navigate, Outlet } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { useAuth } from "./context/auth-context";
import Shell from "./components/Shell";
import LoginPage from "./pages/LoginPage";
import CallbackPage from "./pages/CallbackPage";
import DashboardPage from "./pages/DashboardPage";
import QueuePage from "./pages/QueuePage";
import QueueDetailPage from "./pages/QueueDetailPage";
import OutcomeUnknownPage from "./pages/OutcomeUnknownPage";
import ProjectsPage from "./features/identity/ProjectsPage";
import ProjectDetailPage from "./features/identity/ProjectDetailPage";
import PrincipalsPage from "./features/identity/PrincipalsPage";
import PrincipalDetailPage from "./features/identity/PrincipalDetailPage";
import CredentialsPage from "./features/identity/CredentialsPage";
import RolesPage from "./features/identity/RolesPage";
import ProvidersPage from "./features/catalog/ProvidersPage";
import ProviderAccountsPage from "./features/catalog/ProviderAccountsPage";
import EndpointsPage from "./features/catalog/EndpointsPage";
import QuotasPage from "./features/catalog/QuotasPage";
import ModelsPage from "./features/catalog/ModelsPage";
import RoutesPage from "./features/catalog/RoutesPage";

function RequireAuth() {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-sm text-[var(--ag-text-muted)]">Loading…</p>
      </div>
    );
  }
  if (status === "unauthenticated") return <Navigate to="/login" replace />;
  return <Outlet />;
}

function RedirectIfAuthed() {
  const { status } = useAuth();
  if (status === "authenticated") return <Navigate to="/" replace />;
  return <Outlet />;
}

function AppRoutes() {
  return (
    <Routes>
      <Route element={<RedirectIfAuthed />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/auth/callback" element={<CallbackPage />} />
      </Route>

      <Route element={<RequireAuth />}>
        <Route element={<Shell />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/queue/:requestId" element={<QueueDetailPage />} />
          <Route path="/outcome-unknown" element={<OutcomeUnknownPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
          <Route path="/principals" element={<PrincipalsPage />} />
          <Route path="/principals/:principalId" element={<PrincipalDetailPage />} />
          <Route path="/credentials" element={<CredentialsPage />} />
          <Route path="/roles" element={<RolesPage />} />
          <Route path="/catalog/providers" element={<ProvidersPage />} />
          <Route path="/catalog/provider-accounts" element={<ProviderAccountsPage />} />
          <Route path="/catalog/endpoints" element={<EndpointsPage />} />
          <Route path="/catalog/quotas" element={<QuotasPage />} />
          <Route path="/catalog/models" element={<ModelsPage />} />
          <Route path="/catalog/routes" element={<RoutesPage />} />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
