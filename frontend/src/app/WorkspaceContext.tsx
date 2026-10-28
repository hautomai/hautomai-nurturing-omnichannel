import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useClients } from "../lib/hooks";
import type { Client } from "../lib/types";
import { ErrorState, Skeleton } from "../components/ui/Feedback";

/**
 * v1 is single-tenant (§D2, §22.1): the client switcher is visible but pinned
 * to Acme. The model is multi-client, so the resolution is "first client the
 * token can see" rather than a hard-coded slug — swapping in a real switcher is
 * then a UI change, not a data-model change.
 */
interface WorkspaceState {
  client: Client | null;
  clientId: string | null;
  clients: Client[];
  loading: boolean;
  error: Error | null;
  refetch: () => void;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { data, isLoading, error, refetch } = useClients();

  const value = useMemo<WorkspaceState>(() => {
    const clients = data ?? [];
    // Prefer a client that is not halted so a paused workspace still opens.
    const client = clients.find((row) => !row.sending_halted) ?? clients[0] ?? null;
    return {
      client,
      clientId: client?.id ?? null,
      clients,
      loading: isLoading,
      error: error as Error | null,
      refetch: () => void refetch(),
    };
  }, [data, isLoading, error, refetch]);

  if (value.loading) {
    return (
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (value.error) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6">
        <ErrorState message={value.error.message} onRetry={value.refetch} />
      </div>
    );
  }

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceState {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  return context;
}
