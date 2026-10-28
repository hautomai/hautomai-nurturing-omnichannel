/**
 * Turns the stored `SequenceGraph` into React Flow nodes and edges.
 *
 * The graph is a linked structure (`next`, `on_true`, `on_false`), not a set of
 * coordinates, so positions are derived: nodes are layered by their shortest
 * distance from an entry step, and spread horizontally within a layer. That
 * keeps the layout deterministic — the same graph always draws the same way —
 * which matters when a draft is reloaded and the operator expects to find their
 * canvas where they left it.
 */

import type { Edge, Node } from "@xyflow/react";

import type { SequenceGraph, StepNode } from "../../lib/types";

export const NODE_WIDTH = 248;
export const NODE_HEIGHT = 96;
const X_GAP = 72;
const Y_GAP = 132;

export type StepNodeData = {
  step: StepNode;
  index: number;
  channel: string;
  selected: boolean;
  validation?: { level: "error" | "warning"; message: string }[];
};

export type CanvasNode = Node<StepNodeData, "step">;

export interface CanvasModel {
  nodes: CanvasNode[];
  edges: Edge[];
  order: string[];
}

/** The successor(s) a step points at, in render order. */
export function successorsOf(step: StepNode): { key: string; label: string; branch: boolean }[] {
  const out: { key: string; label: string; branch: boolean }[] = [];
  if (step.type === "condition") {
    if (step.on_true) out.push({ key: step.on_true, label: "true", branch: true });
    if (step.on_false) out.push({ key: step.on_false, label: "false", branch: true });
  } else if (step.next) {
    out.push({ key: step.next, label: "", branch: false });
  }
  return out;
}

export function channelOf(step: StepNode): string {
  if (step.type === "send_email") return "email";
  if (step.type === "send_sms") return "sms";
  if (step.type === "notify_slack" || step.type === "human_task") return "slack";
  return "";
}

export function buildCanvasModel(
  graph: SequenceGraph,
  options: { selectedKey?: string | null; issues?: { step_key: string | null; level: "error" | "warning"; message: string }[] } = {},
): CanvasModel {
  const byKey = new Map<string, StepNode>();
  for (const step of graph.steps ?? []) byKey.set(step.key, step);

  const entries = (graph.entry_step_keys ?? []).filter((key) => byKey.has(key));

  // Breadth-first depth, so a shared successor sits below all of its parents.
  const depth = new Map<string, number>();
  const queue: { key: string; depth: number }[] = entries.map((key) => ({ key, depth: 0 }));
  while (queue.length > 0) {
    const current = queue.shift() as { key: string; depth: number };
    const known = depth.get(current.key);
    if (known !== undefined && known >= current.depth) continue;
    depth.set(current.key, current.depth);
    const step = byKey.get(current.key);
    if (!step) continue;
    for (const successor of successorsOf(step)) {
      queue.push({ key: successor.key, depth: current.depth + 1 });
    }
  }

  // Anything unreachable still draws, below the deepest reachable layer, so a
  // broken draft is visible rather than silently hidden.
  const maxDepth = Math.max(0, ...depth.values());
  for (const key of byKey.keys()) {
    if (!depth.has(key)) depth.set(key, maxDepth + 1);
  }

  const layers = new Map<number, string[]>();
  const order: string[] = [];
  for (const step of graph.steps ?? []) {
    const level = depth.get(step.key) ?? 0;
    layers.set(level, [...(layers.get(level) ?? []), step.key]);
  }
  for (const level of [...layers.keys()].sort((a, b) => a - b)) {
    order.push(...(layers.get(level) ?? []));
  }

  const positions = new Map<string, { x: number; y: number }>();
  for (const [level, keys] of layers) {
    const width = keys.length;
    keys.forEach((key, index) => {
      positions.set(key, {
        x: index * (NODE_WIDTH + X_GAP) - ((width - 1) * (NODE_WIDTH + X_GAP)) / 2,
        y: level * (NODE_HEIGHT + Y_GAP),
      });
    });
  }

  const issuesByStep = new Map<string, { level: "error" | "warning"; message: string }[]>();
  for (const issue of options.issues ?? []) {
    if (!issue.step_key) continue;
    issuesByStep.set(issue.step_key, [
      ...(issuesByStep.get(issue.step_key) ?? []),
      { level: issue.level, message: issue.message },
    ]);
  }

  const nodes: CanvasNode[] = [];
  order.forEach((key, index) => {
    const step = byKey.get(key);
    if (!step) return;
    nodes.push({
      id: key,
      type: "step",
      position: positions.get(key) ?? { x: 0, y: index * (NODE_HEIGHT + Y_GAP) },
      data: {
        step,
        index,
        channel: channelOf(step),
        selected: options.selectedKey === key,
        validation: issuesByStep.get(key),
      },
      draggable: true,
    });
  });

  const edges: Edge[] = [];
  for (const step of graph.steps ?? []) {
    successorsOf(step).forEach((successor, index) => {
      if (!byKey.has(successor.key)) return;
      edges.push({
        id: `${step.key}->${successor.key}:${successor.label}`,
        source: step.key,
        target: successor.key,
        type: "smoothstep",
        animated: false,
        label: successor.label || undefined,
        labelStyle: { fill: "#9A9A94", fontFamily: "IBM Plex Mono", fontSize: 11 },
        labelBgStyle: { fill: "#111111" },
        style: successor.label === "false" ? { strokeDasharray: "4 3" } : undefined,
        markerEnd: { type: "arrowclosed" as never, color: "rgba(245,243,238,.4)" },
        data: { index },
      });
    });
  }

  return { nodes, edges, order };
}

export function emptyGraph(): SequenceGraph {
  return {
    entry_step_keys: ["step_1"],
    steps: [
      {
        key: "step_1",
        type: "send_email",
        name: "Email 1",
        template_key: "",
        fallback: "skip",
        next: "step_exit",
      },
      { key: "step_exit", type: "exit", name: "Exit", reason: "completed" },
    ],
  };
}

/** A unique step key, so duplicating a step never collides. */
export function nextStepKey(graph: SequenceGraph, prefix = "step"): string {
  const keys = new Set((graph.steps ?? []).map((step) => step.key));
  let index = keys.size + 1;
  while (keys.has(`${prefix}_${index}`)) index += 1;
  return `${prefix}_${index}`;
}
