import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Link, useParams } from "react-router-dom";

import { useWorkspace } from "../app/WorkspaceContext";
import { Button } from "../components/ui/Button";
import { Panel } from "../components/ui/Panel";
import { DataTable, BulletList, type Column } from "../components/ui/Data";
import { StatusPill } from "../components/ui/Indicators";
import { ErrorState, Modal } from "../components/ui/Feedback";
import { StepNodeCard } from "../features/canvas/StepNodeCard";
import { StepInspector } from "../features/canvas/StepInspector";
import { SimulateDialog } from "../features/canvas/SimulateDialog";
import {
  buildCanvasModel,
  emptyGraph,
  nextStepKey,
  type CanvasNode,
} from "../features/canvas/layout";
import { api } from "../lib/api";
import { usePublishSequence, useSequence, useTemplates } from "../lib/hooks";
import type {
  PublishResult,
  SequenceGraph,
  SequenceVersion,
  StepNode,
  ValidationIssue,
  ValidateResult,
} from "../lib/types";

const nodeTypes = { step: StepNodeCard };

export function SequenceBuilderPage() {
  return (
    <ReactFlowProvider>
      <SequenceBuilder />
    </ReactFlowProvider>
  );
}

function SequenceBuilder() {
  const { sequenceId = "" } = useParams();
  const { clientId } = useWorkspace();
  const detailQuery = useSequence(sequenceId);
  const templatesQuery = useTemplates(clientId);
  const publish = usePublishSequence(sequenceId);

  const [graph, setGraph] = useState<SequenceGraph | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [validateResult, setValidateResult] = useState<ValidateResult | null>(null);
  const [publishResult, setPublishResult] = useState<PublishResult | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A draft is loaded if one exists; otherwise the published graph is the
  // starting point. `sourceVersionId` tracks what is on screen so switching
  // versions resets the working copy.
  const loadedVersion = detailQuery.data?.draft_version ?? detailQuery.data?.current_version ?? null;
  const sourceVersionId = loadedVersion?.id ?? null;

  useEffect(() => {
    if (!loadedVersion) return;
    setGraph(loadedVersion.graph ?? emptyGraph());
    setSelectedKey(null);
    setIssues([]);
    setValidateResult(null);
    // Keyed on the version id alone: re-rendering with the same version must not
    // clobber unsaved edits.
  }, [sourceVersionId]);

  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);

  const model = useMemo(
    () =>
      graph
        ? buildCanvasModel(graph, {
            selectedKey,
            issues: issues.map((issue) => ({
              step_key: issue.step_key,
              level: issue.level,
              message: issue.message,
            })),
          })
        : { nodes: [], edges: [], order: [] },
    [graph, selectedKey, issues],
  );

  const selectedStep = useMemo(
    () => graph?.steps.find((step) => step.key === selectedKey) ?? null,
    [graph, selectedKey],
  );

  const readOnly = publish.isPending;

  /* --- graph editing --------------------------------------------------- */

  const updateStep = useCallback(
    (key: string, patch: Partial<StepNode>) => {
      setGraph((current) => {
        if (!current) return current;
        const renaming = patch.key !== undefined && patch.key !== key;
        const newKey = renaming ? (patch.key as string) : key;

        const steps = current.steps.map((step) => {
          if (step.key === key) return { ...step, ...patch, key: newKey };
          // Rewrite every pointer that referred to the old key, so renaming a
          // step does not silently orphan its parents.
          if (!renaming) return step;
          const next: StepNode = { ...step };
          if (next.next === key) next.next = newKey;
          if (next.on_true === key) next.on_true = newKey;
          if (next.on_false === key) next.on_false = newKey;
          return next;
        });

        const entry_step_keys = current.entry_step_keys.map((entry) =>
          renaming && entry === key ? newKey : entry,
        );
        const reactions = current.reactions
          ? Object.fromEntries(
              Object.entries(current.reactions).map(([reaction, target]) => [
                reaction,
                renaming && target === key ? newKey : target,
              ]),
            )
          : undefined;

        return { ...current, steps, entry_step_keys, reactions };
      });
      if (patch.key) setSelectedKey(patch.key);
    },
    [],
  );

  const addStep = useCallback(
    (type: StepNode["type"]) => {
      setGraph((current) => {
        const base = current ?? emptyGraph();
        const key = nextStepKey(base, type.replace("send_", "step_"));
        const step: StepNode = { key, type, name: labelFor(type) };
        if (type === "send_email" || type === "send_sms") {
          step.template_key = "";
          step.fallback = "skip";
          step.next = "";
        } else if (type === "delay") {
          step.amount = 3;
          step.unit = "days";
          step.next = "";
        } else if (type === "condition") {
          step.predicate = { type: "replied" };
          step.on_true = "";
          step.on_false = "";
        } else if (type === "notify_slack") {
          step.text = "";
          step.next = "";
        } else if (type === "human_task") {
          step.title = "";
          step.instructions = "";
          step.timeout_hours = 24;
          step.on_timeout = "continue";
          step.next = "";
        } else if (type === "exit") {
          step.reason = "completed";
        }
        setSelectedKey(key);
        return { ...base, steps: [...base.steps, step] };
      });
    },
    [],
  );

  const deleteStep = useCallback(() => {
    if (!selectedKey) return;
    setGraph((current) => {
      if (!current) return current;
      const target = current.steps.find((step) => step.key === selectedKey);
      // Re-link parents to the deleted step's successor so deleting a wait does
      // not cut the chain in half.
      const replacement = target?.next ?? "";
      const steps = current.steps
        .filter((step) => step.key !== selectedKey)
        .map((step) => {
          const next: StepNode = { ...step };
          if (next.next === selectedKey) next.next = replacement;
          if (next.on_true === selectedKey) next.on_true = "";
          if (next.on_false === selectedKey) next.on_false = "";
          return next;
        });
      return {
        ...current,
        steps,
        entry_step_keys: current.entry_step_keys.filter((key) => key !== selectedKey),
      };
    });
    setSelectedKey(null);
  }, [selectedKey]);

  const duplicateStep = useCallback(() => {
    if (!selectedStep) return;
    setGraph((current) => {
      if (!current) return current;
      const key = nextStepKey(current, selectedStep.type.replace("send_", "step_"));
      const copy: StepNode = { ...selectedStep, key, name: `${selectedStep.name} (copy)` };
      return { ...current, steps: [...current.steps, copy] };
    });
  }, [selectedStep]);

  /* --- server actions -------------------------------------------------- */

  async function runValidate(): Promise<ValidateResult | null> {
    if (!graph) return null;
    setBusy("validate");
    setError(null);
    try {
      const result = await api.validate(sequenceId, graph);
      setValidateResult(result);
      setIssues(result.issues);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed.");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function saveDraft() {
    if (!graph) return;
    setBusy("save");
    setError(null);
    try {
      await api.saveDraft(sequenceId, graph, "saved from the builder");
      setNotice("Draft saved. Publish it to put it in front of contacts.");
      await detailQuery.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the draft.");
    } finally {
      setBusy(null);
    }
  }

  async function doPublish() {
    if (!graph) return;
    setBusy("publish");
    setError(null);
    setPublishResult(null);
    try {
      await api.saveDraft(sequenceId, graph, "published from the builder");
      const result = await publish.mutateAsync();
      setPublishResult(result);
      if (result.ok) {
        setNotice(
          `Published v${result.version?.version ?? "?"} — ${result.migrated_enrollments} in-flight contact(s) migrated${result.orphaned_enrollments ? `, ${result.orphaned_enrollments} orphaned` : ""}.`,
        );
        setIssues([]);
      } else {
        setIssues(result.issues);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Publish failed.");
    } finally {
      setBusy(null);
    }
  }

  async function rollback(version: SequenceVersion) {
    setBusy("rollback");
    setError(null);
    try {
      const result = await api.rollback(sequenceId, version.version);
      setNotice(`Rolled back to v${version.version}.`);
      setPublishResult(result);
      await detailQuery.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed.");
    } finally {
      setBusy(null);
    }
  }

  const onNodeClick: NodeMouseHandler<CanvasNode> = (_event, node) => setSelectedKey(node.id);

  if (detailQuery.isLoading) {
    return (
      <div className="mx-auto max-w-[1600px]">
        <Panel title="Loading sequence">…</Panel>
      </div>
    );
  }

  if (detailQuery.error) {
    return (
      <div className="mx-auto max-w-3xl">
        <ErrorState message={(detailQuery.error as Error).message} onRetry={() => detailQuery.refetch()} />
      </div>
    );
  }

  const sequence = detailQuery.data?.sequence;
  const versions = (detailQuery.data?.versions ?? []).filter((version) => version.published_at);
  const blocking = issues.filter((issue) => issue.level === "error");
  const warnings = issues.filter((issue) => issue.level === "warning");

  return (
    <div className="mx-auto flex w-full max-w-[1800px] flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <Link to="/sequences" className="font-mono text-[11px] text-meta hover:text-ink">
            ← Sequences
          </Link>
          <h1 className="mt-1 flex items-center gap-3 font-display text-[24px] font-bold tracking-title text-ink">
            {sequence?.name ?? "Sequence"}
            <StatusPill
              tone={sequence?.status === "active" ? "accent" : "muted"}
              label={sequence?.status ?? "draft"}
              glow={sequence?.status === "active"}
            />
          </h1>
          <p className="mt-1 font-mono text-[11px] text-meta">
            {detailQuery.data?.draft_version
              ? `Editing draft v${detailQuery.data.draft_version.version}`
              : `Editing published v${detailQuery.data?.current_version?.version ?? "—"}`}
            {" · "}
            {detailQuery.data?.versions.length ?? 0} version(s)
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={runValidate} loading={busy === "validate"}>
            Validate
          </Button>
          <Button size="sm" onClick={saveDraft} loading={busy === "save"} disabled={readOnly}>
            Save draft
          </Button>
          <Button size="sm" onClick={() => setSimulating(true)} disabled={!graph}>
            Dry run
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={doPublish}
            loading={busy === "publish"}
            disabled={readOnly || blocking.length > 0}
            title={blocking.length > 0 ? "Fix blocking errors first" : "Publish a new immutable version"}
          >
            Publish
          </Button>
        </div>
      </div>

      {notice && (
        <p role="status" className="rounded-card border border-[var(--ghost-border)] bg-[var(--ghost-fill)] px-4 py-2.5 font-mono text-[12px] text-accent">
          {notice}
        </p>
      )}
      {error && <ErrorState message={error} />}

      {(blocking.length > 0 || warnings.length > 0) && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {blocking.length > 0 && (
            <Panel kicker="Blocking" title={`${blocking.length} error(s) stop publish`}>
              <BulletList
                items={blocking.map((issue) => ({ text: issue.message, tone: "danger" as const }))}
              />
            </Panel>
          )}
          {warnings.length > 0 && (
            <Panel kicker="Warnings" title={`${warnings.length} warning(s)`}>
              <BulletList
                items={warnings.map((issue) => ({ text: issue.message, tone: "warm" as const }))}
              />
            </Panel>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[2.2fr_1fr]">
        <Panel
          kicker="Canvas"
          title="Sequence graph"
          bodyClassName="p-0"
          actions={
            <>
              <Button size="sm" onClick={() => addStep("send_email")}>
                + Email
              </Button>
              <Button size="sm" onClick={() => addStep("send_sms")}>
                + SMS
              </Button>
              <Button size="sm" onClick={() => addStep("delay")}>
                + Wait
              </Button>
              <Button size="sm" onClick={() => addStep("condition")}>
                + Condition
              </Button>
              <Button size="sm" onClick={() => addStep("exit")}>
                + Exit
              </Button>
            </>
          }
          footer={["Drag to arrange, click to inspect", `${model.nodes.length} steps`]}
        >
          <div className="h-[620px] w-full">
            <ReactFlow
              nodes={model.nodes}
              edges={model.edges}
              nodeTypes={nodeTypes}
              onNodeClick={onNodeClick}
              onPaneClick={() => setSelectedKey(null)}
              fitView
              proOptions={{ hideAttribution: true }}
              nodesDraggable
              nodesConnectable={false}
              colorMode="dark"
            >
              <Background variant={BackgroundVariant.Dots} gap={90} size={1} color="rgba(255,255,255,.06)" />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable nodeColor="#171717" maskColor="rgba(0,0,0,.6)" />
            </ReactFlow>
          </div>
        </Panel>

        <div className="flex min-w-0 flex-col gap-4">
          {selectedStep && graph ? (
            <StepInspector
              step={selectedStep}
              graph={graph}
              templates={templates}
              issues={issues
                .filter((issue) => issue.step_key === selectedStep.key)
                .map((issue) => ({ level: issue.level, message: issue.message }))}
              readOnly={readOnly}
              onChange={(patch) => updateStep(selectedStep.key, patch)}
              onDelete={deleteStep}
              onDuplicate={duplicateStep}
            />
          ) : (
            <Panel kicker="Inspector" title="No step selected">
              <p className="font-mono text-[12.5px] text-body-2">
                Click a node on the canvas to edit its template, wait, branch or exit reason. Add
                steps with the buttons above the canvas.
              </p>
            </Panel>
          )}

          <VersionPanel
            versions={versions}
            versionsAll={detailQuery.data?.versions ?? []}
            validateResult={validateResult}
            publishResult={publishResult}
            busy={busy}
            onRollback={rollback}
          />
        </div>
      </div>

      {graph && (
        <SimulateDialog
          open={simulating}
          onClose={() => setSimulating(false)}
          sequenceId={sequenceId}
          clientId={clientId}
        />
      )}

      <Modal
        open={Boolean(publishResult && !publishResult.ok)}
        title="Publish blocked"
        onClose={() => setPublishResult(null)}
        footer={<Button onClick={() => setPublishResult(null)}>OK</Button>}
      >
        <BulletList
          items={(publishResult?.issues ?? [])
            .filter((issue) => issue.level === "error")
            .map((issue) => ({ text: issue.message, tone: "danger" as const }))}
        />
      </Modal>
    </div>
  );
}

function VersionPanel({
  versions,
  versionsAll,
  validateResult,
  publishResult,
  busy,
  onRollback,
}: {
  versions: SequenceVersion[];
  versionsAll: SequenceVersion[];
  validateResult: ValidateResult | null;
  publishResult: PublishResult | null;
  busy: string | null;
  onRollback: (version: SequenceVersion) => void;
}) {
  const columns: Column<SequenceVersion>[] = [
    { key: "version", header: "Version", render: (row) => `v${row.version}` },
    {
      key: "status",
      header: "Status",
      render: (row) =>
        row.published_at ? (
          <StatusPill tone="accent" label="published" showsCheck />
        ) : (
          <StatusPill tone="muted" label="draft" />
        ),
    },
    {
      key: "created",
      header: "Created",
      align: "right",
      render: (row) => new Date(row.created_at).toLocaleDateString(),
    },
    {
      key: "action",
      header: "",
      align: "right",
      render: (row) =>
        row.published_at ? (
          <Button size="sm" onClick={() => onRollback(row)} loading={busy === "rollback"}>
            Roll back
          </Button>
        ) : (
          <span className="font-mono text-[11px] text-meta-2">draft</span>
        ),
    },
  ];

  return (
    <Panel
      kicker="Versions"
      title="Immutable history"
      footer={[
        validateResult ? (validateResult.ok ? "Validation passed" : "Validation found errors") : "Not validated yet",
        publishResult?.ok ? `Published v${publishResult.version?.version ?? ""}` : "",
      ]}
    >
      <DataTable
        columns={columns}
        rows={[...versionsAll].sort((a, b) => b.version - a.version)}
        rowKey={(row) => row.id}
        dense
        empty={<p className="font-mono text-[12.5px] text-body-2">No versions yet.</p>}
      />
      <p className="mt-3 font-mono text-[11px] leading-relaxed text-meta-2">
        Publishing creates a new immutable version and migrates in-flight contacts to it; contacts
        whose current step no longer exists are orphaned and logged. {versions.length} published.
      </p>
    </Panel>
  );
}

function labelFor(type: StepNode["type"]): string {
  switch (type) {
    case "send_email":
      return "New email step";
    case "send_sms":
      return "New SMS step";
    case "delay":
      return "Wait";
    case "condition":
      return "Branch";
    case "notify_slack":
      return "Slack alert";
    case "human_task":
      return "Human task";
    case "exit":
      return "Exit";
  }
}
