import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { api } from "../lib/api";
import { useContacts, useEnrollments, useImportContacts, useSequence, useSequences } from "../lib/hooks";
import { dateOnly, initials } from "../lib/format";
import { Button } from "../components/ui/Button";
import { Panel, PanelSkeleton } from "../components/ui/Panel";
import { DataTable, type Column } from "../components/ui/Data";
import { StatusPill } from "../components/ui/Indicators";
import { EmptyState, ErrorState, Modal } from "../components/ui/Feedback";
import { Field, Input, Select } from "../components/ui/Fields";
import type { Contact } from "../lib/types";

const IMPORT_FIELDS = [
  { key: "email", label: "Email column" },
  { key: "phone_e164", label: "Phone column" },
  { key: "first_name", label: "First name column" },
  { key: "last_name", label: "Last name column" },
  { key: "company", label: "Company column" },
  { key: "job_title", label: "Job title column" },
  { key: "timezone", label: "Timezone column" },
] as const;

export function ContactsPage() {
  const { clientId } = useWorkspace();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const stepFilter = params.get("step");
  const sequenceFilter = params.get("sequence");
  const search = params.get("q") ?? "";

  const sequences = useSequences(clientId);
  const contacts = useContacts(clientId, { search: search || undefined, limit: 500 });
  const sequence = useSequence(stepFilter ? sequenceFilter : null);
  const enrollments = useEnrollments(clientId, stepFilter && sequenceFilter ? { sequence_id: sequenceFilter } : {});

  const [importing, setImporting] = useState(false);

  /**
   * Drill-through from the funnel (§22.2): keep contacts whose enrollment is at
   * or past the clicked step. Order comes from the graph, so a step inserted
   * before the clicked one shifts the window the way an operator expects.
   */
  const filteredContacts = useMemo(() => {
    const rows = contacts.data ?? [];
    if (!stepFilter) return rows;

    const graph = sequence.data?.current_version?.graph ?? sequence.data?.draft_version?.graph;
    const order = (graph?.steps ?? []).map((step) => step.key);
    const targetIndex = order.indexOf(stepFilter);

    const matchingIds = new Set(
      (enrollments.data ?? [])
        .filter((enrollment) => {
          if (!enrollment.current_step_key) return false;
          const currentIndex = order.indexOf(enrollment.current_step_key);
          if (targetIndex === -1 || currentIndex === -1) {
            return enrollment.current_step_key === stepFilter;
          }
          return currentIndex >= targetIndex;
        })
        .map((enrollment) => enrollment.contact_id),
    );

    return rows.filter((contact) => matchingIds.has(contact.id));
  }, [contacts.data, enrollments.data, sequence.data, stepFilter]);

  const patchParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace: true });
  };

  const columns: Column<Contact>[] = [
    {
      key: "name",
      header: "Contact",
      render: (row) => (
        <span className="flex items-center gap-3">
          <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line font-mono text-[11px] text-body-2">
            {initials(row.first_name, row.last_name, row.email)}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-ink">
              {[row.first_name, row.last_name].filter(Boolean).join(" ") || "—"}
            </span>
            <span className="block truncate font-mono text-[11px] text-meta">
              {row.email ?? row.phone_e164 ?? "no channel"}
            </span>
          </span>
        </span>
      ),
    },
    { key: "company", header: "Company", render: (row) => row.company || "—" },
    { key: "title", header: "Title", render: (row) => row.job_title || "—" },
    {
      key: "channels",
      header: "Channels",
      render: (row) => (
        <span className="flex gap-1.5">
          {row.email && <StatusPill tone="accent" label="email" />}
          {row.phone_e164 && <StatusPill tone="warm" label="sms" />}
          {!row.email && !row.phone_e164 && <StatusPill tone="danger" label="unreachable" />}
        </span>
      ),
    },
    { key: "source", header: "Source", render: (row) => row.source },
    { key: "created", header: "Added", align: "right", render: (row) => dateOnly(row.created_at) },
  ];

  const hasFilter = Boolean(stepFilter || search);

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            aria-label="Search contacts"
            placeholder="Search name, email or company"
            value={search}
            onChange={(event) => patchParams({ q: event.target.value || null })}
            className="w-72"
          />
          {hasFilter && (
            <Button size="sm" onClick={() => patchParams({ step: null, q: null, sequence: null })}>
              Clear filters
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setImporting(true)}>
            Import CSV
          </Button>
        </div>
      </div>

      {stepFilter && (
        <div className="flex flex-wrap items-center gap-3 rounded-card border border-accent bg-[var(--ghost-fill)] px-4 py-3">
          <StatusPill tone="accent" label="Funnel drill-through" />
          <span className="font-mono text-[12px] text-body-2">
            Showing contacts at or past <span className="text-ink">{stepFilter}</span>
            {sequenceFilter ? ` in ${sequence.data?.sequence.name ?? "sequence"}` : ""}
          </span>
          <Button size="sm" onClick={() => patchParams({ step: null, sequence: null })}>
            Remove
          </Button>
        </div>
      )}

      {contacts.error && (
        <ErrorState message={(contacts.error as Error).message} onRetry={() => contacts.refetch()} />
      )}

      <Panel
        kicker="Contacts"
        title={`${filteredContacts.length} contact(s)`}
        bodyClassName="px-0 py-0"
        footer={["Click a row for the full timeline", sequenceFilter ? `sequence ${sequenceFilter}` : ""]}
      >
        {contacts.isLoading ? (
          <div className="p-5">
            <PanelSkeleton rows={6} />
          </div>
        ) : filteredContacts.length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="No contacts match"
              description="Import a CSV, add contacts through the API, or clear the filters."
              steps={[
                { title: "Import contacts", body: "CSV upload with explicit column mapping." },
                { title: "POST /api/contacts", body: "Drive it from Instantly, n8n or a form." },
                { title: "GoHighLevel sync", body: "The CRM adapter pulls contacts on a schedule." },
              ]}
            />
          </div>
        ) : (
          <DataTable
            columns={columns}
            rows={filteredContacts}
            rowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/contacts/${row.id}`)}
          />
        )}
      </Panel>

      <ImportDialog
        open={importing}
        onClose={() => setImporting(false)}
        clientId={clientId}
        sequences={(sequences.data ?? []).map((row) => ({ id: row.id, name: row.name }))}
      />

      <p className="font-mono text-[11px] text-meta-2">
        Need the API shape? <Link className="text-body-2 underline" to="/settings">Settings</Link> lists
        the intake endpoints and webhook URLs.
      </p>
    </div>
  );
}

function ImportDialog({
  open,
  onClose,
  clientId,
  sequences,
}: {
  open: boolean;
  onClose: () => void;
  clientId: string | null;
  sequences: { id: string; name: string }[];
}) {
  const importMutation = useImportContacts(clientId ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [sequenceId, setSequenceId] = useState("");
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.importContacts>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onFileChange(selected: File | null) {
    setFile(selected);
    setResult(null);
    setError(null);
    if (!selected) {
      setHeaders([]);
      return;
    }
    const text = await selected.slice(0, 4096).text();
    const firstLine = text.split(/\r?\n/)[0] ?? "";
    const columns = firstLine.split(",").map((value) => value.trim().replace(/^"|"$/g, ""));
    setHeaders(columns);
    // Best-effort default mapping: find a column whose name looks like the field.
    const guessed: Record<string, string> = {};
    for (const field of IMPORT_FIELDS) {
      const alias = field.key.split("_")[0];
      const match = columns.find((column) => column.toLowerCase().replace(/[^a-z]/g, "").includes(alias));
      if (match) guessed[field.key] = match;
    }
    setMapping(guessed);
  }

  async function submit() {
    if (!file) {
      setError("Choose a CSV file first.");
      return;
    }
    if (!mapping.email && !mapping.phone_e164) {
      setError("Map at least the email or the phone column.");
      return;
    }
    setError(null);
    try {
      const outcome = await importMutation.mutateAsync({
        file,
        mapping,
        enrollSequenceId: sequenceId || undefined,
      });
      setResult(outcome);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed.");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import contacts"
      width="max-w-2xl"
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button variant="primary" onClick={submit} loading={importMutation.isPending}>
            Import
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="CSV file" htmlFor="import-file" error={error}>
          <input
            id="import-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => void onFileChange(event.target.files?.[0] ?? null)}
            className="w-full cursor-pointer rounded-input border border-line bg-[var(--field-fill)] px-3 py-2 font-mono text-[12px] text-body-2 file:mr-3 file:rounded-button file:border-0 file:bg-[var(--ghost-fill)] file:px-3 file:py-1 file:font-mono file:text-accent"
          />
        </Field>

        {headers.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {IMPORT_FIELDS.map((field) => (
              <Field key={field.key} label={field.label} htmlFor={`map-${field.key}`}>
                <Select
                  id={`map-${field.key}`}
                  value={mapping[field.key] ?? ""}
                  onChange={(event) =>
                    setMapping((current) => ({ ...current, [field.key]: event.target.value }))
                  }
                >
                  <option value="">— not mapped —</option>
                  {headers.map((header) => (
                    <option key={header} value={header}>
                      {header}
                    </option>
                  ))}
                </Select>
              </Field>
            ))}
          </div>
        )}

        <Field label="Enroll into sequence" htmlFor="import-sequence" hint="Optional — enrolls every imported contact.">
          <Select
            id="import-sequence"
            value={sequenceId}
            onChange={(event) => setSequenceId(event.target.value)}
          >
            <option value="">— do not enroll —</option>
            {sequences.map((sequence) => (
              <option key={sequence.id} value={sequence.id}>
                {sequence.name}
              </option>
            ))}
          </Select>
        </Field>

        {result && (
          <div className="flex flex-wrap items-center gap-3 rounded-card border border-line px-4 py-3">
            <StatusPill tone="accent" label={`${result.created} created`} />
            <StatusPill tone="muted" label={`${result.updated} updated`} />
            <StatusPill tone={result.rejected.length > 0 ? "warm" : "muted"} label={`${result.rejected.length} rejected`} />
            <StatusPill tone={result.enrolled > 0 ? "accent" : "muted"} label={`${result.enrolled} enrolled`} />
          </div>
        )}
      </div>
    </Modal>
  );
}
