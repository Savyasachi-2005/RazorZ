import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { AuditPage } from "./pages/AuditPage";
import { CopilotPage } from "./pages/CopilotPage";
import { ExceptionDetailPage } from "./pages/ExceptionDetailPage";
import { ExceptionsPage } from "./pages/ExceptionsPage";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ReconciliationPage } from "./pages/ReconciliationPage";
import { SourcesPage } from "./pages/SourcesPage";
import { AppDataProvider } from "./state/AppDataContext";
import { useAuth } from "./state/AuthContext";

export default function App() {
  const { user, checking } = useAuth();

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-ink-muted">
        Restoring session…
      </div>
    );
  }

  // Unauthenticated: the login screen is the only reachable view, and no
  // dashboard data request is ever issued.
  if (!user) return <LoginPage />;

  return (
    <AppDataProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="reconciliation" element={<ReconciliationPage />} />
          <Route path="exceptions" element={<ExceptionsPage />} />
          <Route path="exceptions/:exceptionId" element={<ExceptionDetailPage />} />
          <Route path="copilot" element={<CopilotPage />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </AppDataProvider>
  );
}
