import { useWorkspace } from "../app/WorkspaceContext";
import { useAuth } from "../app/AuthContext";
import { useMeta, useSetHalt } from "../lib/hooks";
import { Button } from "../components/ui/Button";
import { Panel } from "../components/ui/Panel";
import { BulletList, KeyValue } from "../components/ui/Data";
import { Banner, StatusPill } from "../components/ui/Indicators";

const WEBHOOKS = [
  { path: "POST /api/webhooks/email/inbound", note: "Replies and bounces from the SMTP pool" },
  { path: "POST /api/webhooks/twilio/status", note: "SMS delivery and failure callbacks" },
  { path: "POST /api/webhooks/twilio/inbound", note: "Inbound SMS replies and STOP" },
  { path: "POST /api/webhooks/slack/actions", note: "Interactive human-task buttons" },
  { path: "POST /api/webhooks/ghl", note: "GoHighLevel appointments, opportunities and tags" },
];

const INTAKE = [
  "POST /api/clients/{client_id}/contacts — add one contact",
  "POST /api/clients/{client_id}/contacts/import — CSV upload with column mapping",
  "POST /api/enrollments — enroll a contact in a sequence",
  "GET  /api/contacts/{contact_id}/timeline — the full cross-channel history",
];

export function SettingsPage() {
  const { client } = useWorkspace();
  const { user, logout } = useAuth();
  const { data: meta } = useMeta();
  const setHalt = useSetHalt(client?.id ?? "");

  return (
    <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-5">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Panel kicker="Workspace" title={client?.name ?? "No client"}>
          {client ? (
            <div>
              <KeyValue label="Slug" value={client.slug} />
              <KeyValue label="Timezone" value={client.default_timezone} />
              <KeyValue label="Sending" value={client.sending_halted ? "halted" : "running"} />
              <KeyValue label="Halt reason" value={client.halt_reason ?? "—"} />
              <div className="mt-3">
                <StatusPill
                  tone={client.sending_halted ? "danger" : "accent"}
                  label={client.sending_halted ? "halted" : "sending"}
                  glow={!client.sending_halted}
                />
              </div>
              <div className="mt-4 flex gap-2">
                {client.sending_halted ? (
                  <Button
                    variant="primary"
                    size="sm"
                    loading={setHalt.isPending}
                    onClick={() => setHalt.mutate({ sending_halted: false })}
                  >
                    Lift halt and resume
                  </Button>
                ) : (
                  <Button
                    variant="danger"
                    size="sm"
                    loading={setHalt.isPending}
                    onClick={() =>
                      setHalt.mutate({
                        sending_halted: true,
                        halt_reason: "halted from settings",
                      })
                    }
                  >
                    Pause all sends
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <p className="font-mono text-[12.5px] text-body-2">No client visible to this token.</p>
          )}
        </Panel>

        <Panel kicker="Signed in" title={user?.name ?? "Operator"}>
          <KeyValue label="Email" value={user?.email ?? "—"} />
          <KeyValue label="Role" value={user?.role ?? "—"} />
          <div className="mt-4">
            <Button size="sm" onClick={logout}>
              Sign out
            </Button>
          </div>
        </Panel>

        <Panel kicker="Send policy" title="Cadence and compliance limits">
          {meta ? (
            <div>
              <KeyValue label="Min hours between touches" value={String(meta.policy.min_hours_between_touches)} />
              <KeyValue label="Max touches per enrollment" value={String(meta.policy.max_touches)} />
              <KeyValue label="Max SMS touches" value={String(meta.policy.max_sms)} />
              <KeyValue
                label="SMS quiet hours"
                value={
                  meta.policy.sms_quiet_hours
                    ? `${String(meta.policy.sms_window[0]).padStart(2, "0")}:00–${String(meta.policy.sms_window[1]).padStart(2, "0")}:00`
                    : "disabled"
                }
              />
              <KeyValue label="Weekend SMS" value={meta.policy.sms_allow_weekends ? "allowed" : "blocked"} />
              <KeyValue label="Reaction policy" value={meta.reaction_policy} />
            </div>
          ) : (
            <p className="font-mono text-[12.5px] text-body-2">Loading policy…</p>
          )}
        </Panel>

        <Panel kicker="Integrations" title="Channel and CRM mode">
          {meta ? (
            <div>
              <KeyValue label="Channel mode" value={meta.channel_mode} />
              <KeyValue label="CRM mode" value={meta.crm_mode} />
              <KeyValue label="Environment" value={meta.app_env} />
              <div className="mt-3">
                <BulletList
                  items={Object.entries(meta.channels).map(([name, state]) => ({
                    text: `${name}: ${state}`,
                    tone: "muted" as const,
                  }))}
                />
              </div>
              {meta.channel_mode === "fake" && (
                <div className="mt-3">
                  <Banner tone="warm" title="Fake adapters are active">
                    Nothing leaves this process. Set CHANNEL_MODE=smtp and CRM_MODE=ghl with real
                    credentials to send for real.
                  </Banner>
                </div>
              )}
            </div>
          ) : (
            <p className="font-mono text-[12.5px] text-body-2">Loading integrations…</p>
          )}
        </Panel>

        <Panel kicker="Intake" title="Drive the engine from outside">
          <BulletList items={INTAKE.map((text) => ({ text, tone: "muted" as const }))} />
          <p className="mt-3 font-mono text-[11px] text-meta-2">
            Interactive OpenAPI docs are served by the API at <code>/docs</code>.
          </p>
        </Panel>

        <Panel kicker="Webhooks" title="Callbacks the engine consumes">
          <BulletList items={WEBHOOKS.map((hook) => ({ text: hook.path, tone: "accent" as const }))} />
          <p className="mt-3 font-mono text-[11px] text-meta-2">
            All webhook routes are signature-verified with <code>WEBHOOK_SECRET</code>.
          </p>
        </Panel>
      </div>

      <Panel kicker="Tracking" title="What v1 does not observe">
        <BulletList
          items={[
            { text: "No open pixels — opens are unobservable and unreliable.", tone: "muted" as const },
            { text: "No link rewriting — clicks would change every URL in your copy.", tone: "muted" as const },
            { text: "Branches key off replies, silence and meeting signals instead.", tone: "accent" as const },
          ]}
        />
      </Panel>
    </div>
  );
}
