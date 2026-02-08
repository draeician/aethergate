import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import Shell from "./components/Shell";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import UsersPage from "./pages/UsersPage";
import KeysPage from "./pages/KeysPage";
import EndpointsPage from "./pages/EndpointsPage";
import ModelsPage from "./pages/ModelsPage";
import LogsPage from "./pages/LogsPage";

function ProtectedRoutes() {
  const { adminKey } = useAuth();
  if (!adminKey) return <Navigate to="/login" replace />;

  return (
    <Shell>
      {/* Outlet is rendered inside Shell */}
    </Shell>
  );
}

function AppRoutes() {
  const { adminKey } = useAuth();

  return (
    <Routes>
      <Route
        path="/login"
        element={adminKey ? <Navigate to="/" replace /> : <LoginPage />}
      />
      <Route element={<ProtectedRoutes />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/keys" element={<KeysPage />} />
        <Route path="/endpoints" element={<EndpointsPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/logs" element={<LogsPage />} />
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
