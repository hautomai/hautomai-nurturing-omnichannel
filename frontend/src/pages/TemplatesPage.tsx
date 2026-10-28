import { useEffect, useMemo, useState } from "react";

import { useWorkspace } from "../app/WorkspaceContext";
import { useSaveTemplate, useTemplates } from "../lib/hooks";
import { Button } from "../components/ui/Button";
import { Panel, PanelSkeleton } from "../components/ui/Panel";
import { BulletList, ChannelTag } from "../components/ui/Data";
import { StatusPill } from "../components/ui/Indicators";
import { EmptyState, ErrorState } from "../components/ui/Feedback";
import { Field, Input, Textarea } from "../components/ui/Fields";
import type { Template } from "../lib/types";

/** Merge fields the engine resolves at send time (§11). */
const MERGE_FIELDS = [
  "{{contact.first_name}}",
  "{{contact.company}}",
  "{{contact.job_title}}",
  "{{persona.from_name}}",
  "{{client.name}}",
  "{{signal.role_title}}",
];

const SAMPLE: Record<string, string> = {
  "contact.first_name": "Alex",
  "contact.company": "Orbit Hardware",
  "contact.job_title": "Head of Engineering",
  "persona.from_name": "Dana Tan",
  "client.name": "Acme innovation",
  "signal.role_title": "Senior Product Engineer",
};

function render(text: string): string {
  return text.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (match, key: string) => SAMPLE[key] ?? match);
}

export function TemplatesPage() {
  const { clientId } = useWorkspace();
  const templates = useTemplates(clientId);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const rows = useMemo(() => templates.data ?? [], [templates.data]);
  const selected = rows.find((row) => row.id === selectedId) ?? rows[0] ?? null;

  useEffect(() => {
    if (!selectedId && rows.length > 0) setSelectedId(rows[0].id);
  }, [rows, selectedId]);

  const email = rows.filter((row) => row.channel === "email");
  const sms = rows.filter((row) => row.channel === "sms");

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-[12px] text-body-2">
          Copy is authored here, never generated at runtime. Every template is plain text plus merge
          fields, so a failed substitution is visible in preview before it ships.
        </p>
      </div>

      {templates.error && (
        <ErrorState message={(templates.error as Error).message} onRetry={() => templates.refetch()} />
      )}

      {templates.isLoading ? (
        <Panel title="Templates">
          <PanelSkeleton rows={5} />
        </Panel>
      ) : rows.length === 0 ? (
        <Panel title="Templates">
          <EmptyState
            title="No templates yet"
            description="Seed the workspace to load the Acme copy, or add templates through the API."
          />
        </Panel>
      ) : (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[320px_1fr]">
          <Panel kicker="Library" title={`${rows.length} template(s)`} bodyClassName="px-2 py-2">
            {[
              { label: "Email", items: email, tone: "accent" as const },
              { label: "SMS", items: sms, tone: "warm" as const },
            ].map((group) => (
              <div key={group.label} className="mb-2">
                <p className="px-2 py-1 font-mono text-[10px] uppercase tracking-kicker text-meta">
                  {group.label} · {group.items.length}
                </p>
                <ul className="flex flex-col">
                  {group.items.map((template) => (
                    <li key={template.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(template.id)}
                        className={
                          "w-full rounded-input border-l-2 px-2 py-2 text-left transition-colors " +
                          (selected?.id === template.id
                            ? "border-l-accent bg-panel-2"
                            : "border-l-transparent hover:bg-[rgba(255,255,255,.03)]")
                        }
                      >
                        <span className="block truncate font-mono text-[12.5px] text-ink">
                          {template.name || template.key}
                        </span>
                        <span className="block truncate font-mono text-[11px] text-meta">
                          {template.key}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </Panel>

          {selected && <TemplateEditor key={selected.id} template={selected} clientId={clientId ?? ""} />}
        </div>
      )}
    </div>
  );
}

function TemplateEditor({ template, clientId }: { template: Template; clientId: string }) {
  const save = useSaveTemplate(clientId);
  const [subject, setSubject] = useState(template.subject);
  const [body, setBody] = useState(template.body);
  const [saved, setSaved] = useState<string | null>(null);

  const dirty = subject !== template.subject || body !== template.body;

  useEffect(() => {
    setSubject(template.subject);
    setBody(template.body);
    setSaved(null);
  }, [template.id, template.subject, template.body]);

  return (
    <Panel
      kicker={template.channel}
      title={template.name || template.key}
      actions={
        <>
          <ChannelTag channel={template.channel} />
          <Button
            size="sm"
            variant="primary"
            disabled={!dirty}
            loading={save.isPending}
            onClick={() =>
              save.mutate(
                { id: template.id, body: { subject, body } },
                { onSuccess: () => setSaved("Saved.") },
              )
            }
          >
            Save
          </Button>
        </>
      }
      footer={[template.key, saved ?? (dirty ? "Unsaved changes" : "Up to date")]}
    >
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          {template.channel === "email" && (
            <Field label="Subject" htmlFor="template-subject">
              <Input
                id="template-subject"
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
              />
            </Field>
          )}
          <Field
            label={template.channel === "email" ? "Body (HTML)" : "Message"}
            htmlFor="template-body"
          >
            <Textarea
              id="template-body"
              value={body}
              onChange={(event) => setBody(event.target.value)}
              className="min-h-64"
            />
          </Field>
          {save.error && <ErrorState message={(save.error as Error).message} />}
        </div>

        <div className="flex flex-col gap-4">
          <div>
            <p className="mb-2 font-mono text-[11px] uppercase tracking-kicker text-meta">Preview</p>
            <div className="rounded-card border border-line bg-[var(--panel-fill)] px-4 py-3">
              {template.channel === "email" && (
                <p className="mb-2 font-mono text-[13px] font-semibold text-ink">
                  {render(subject) || "(no subject)"}
                </p>
              )}
              <div
                className="font-mono text-[12.5px] leading-relaxed text-body-2 [&_p]:mb-2"
                // Preview only: the body is authored by operators in this app, and
                // rendering it as text would hide the HTML they actually send.
                dangerouslySetInnerHTML={{ __html: render(body) || "<p>(empty)</p>" }}
              />
            </div>
          </div>

          <div>
            <p className="mb-2 font-mono text-[11px] uppercase tracking-kicker text-meta">
              Available merge fields
            </p>
            <BulletList items={MERGE_FIELDS.map((field) => ({ text: field, tone: "muted" as const }))} />
          </div>

          <div className="flex items-center gap-2">
            <StatusPill
              tone={/\{\{\s*[\w.]+\s*\}\}/.test(subject + body) ? "accent" : "muted"}
              label={/\{\{\s*[\w.]+\s*\}\}/.test(subject + body) ? "merge fields detected" : "no merge fields"}
            />
          </div>
        </div>
      </div>
    </Panel>
  );
}
