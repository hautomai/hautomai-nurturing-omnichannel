import { QueryClient, QueryClientProvider, QueryCache } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AuthProvider, useAuth } from "./AuthContext";
import { WorkspaceProvider } from "./WorkspaceContext";
import { AppShell } from "../components/layout/AppShell";
import { LoginPage } from "../pages/LoginPage";
import { DashboardPage } from "../pages/DashboardPage";
import { SequencesPage } from "../pages/SequencesPage";
import { SequenceBuilderPage } from "../pages/SequenceBuilderPage";
import { ContactsPage } from "../pages/ContactsPage";
import { ContactTimelinePage } from "../pages/ContactTimelinePage";
import { InboxesPage } from "../pages/InboxesPage";
import { TemplatesPage } from "../pages/TemplatesPage";
import { AnalyticsPage } from "../pages/AnalyticsPage";
import { SettingsPage } from "../pages/SettingsPage";
import { ApiError } from "../lib/api";
import { Skeleton } from "../components/ui/Feedback";

/**
 * A 401 means the token expired: drop it and let the auth gate show the login
 * screen rather than retrying forever. Everything else retries once.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status === 401) return false;
        return failureCount < 1;
      },
    },
  },
  queryCache: new QueryCache({
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        localStorage.removeItem("hautomai.token");
      }
    },
  }),
});

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 p-6">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />;

  return (
    <WorkspaceProvider>
      <AppShell>{children}</AppShell>
    </WorkspaceProvider>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <DashboardPage />
              </RequireAuth>
            }
          />
          <Route
            path="/sequences"
            element={
              <RequireAuth>
                <SequencesPage />
              </RequireAuth>
            }
          />
          <Route
            path="/sequences/:sequenceId"
            element={
              <RequireAuth>
                <SequenceBuilderPage />
              </RequireAuth>
            }
          />
          <Route
            path="/contacts"
            element={
              <RequireAuth>
                <ContactsPage />
              </RequireAuth>
            }
          />
          <Route
            path="/contacts/:contactId"
            element={
              <RequireAuth>
                <ContactTimelinePage />
              </RequireAuth>
            }
          />
          <Route
            path="/inboxes"
            element={
              <RequireAuth>
                <InboxesPage />
              </RequireAuth>
            }
          />
          <Route
            path="/templates"
            element={
              <RequireAuth>
                <TemplatesPage />
              </RequireAuth>
            }
          />
          <Route
            path="/analytics"
            element={
              <RequireAuth>
                <AnalyticsPage />
              </RequireAuth>
            }
          />
          <Route
            path="/settings"
            element={
              <RequireAuth>
                <SettingsPage />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </QueryClientProvider>
  );
}
