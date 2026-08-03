"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api, ClarificationQuestion, WorkflowStep } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  CheckCircle2,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Play,
  FileCheck,
  Beaker,
  Info,
  HelpCircle,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

function StepItem({
  step,
  onToggle,
}: {
  step: WorkflowStep;
  onToggle: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const isContested = step.classification === "contested";

  return (
    <motion.div
      layout
      className={`rounded-lg border transition-all ${
        !step.enabled
          ? "border-border/30 opacity-50"
          : isContested
          ? "border-amber-500/30 bg-amber-500/5"
          : "border-border/50 bg-card/30"
      }`}
    >
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Toggle checkbox */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle(step.id);
          }}
          className={`flex h-5 w-5 items-center justify-center rounded border transition-all ${
            step.enabled
              ? isContested
                ? "border-amber-500 bg-amber-500/20"
                : "border-teal-500 bg-teal-500/20"
              : "border-border"
          }`}
        >
          {step.enabled && (
            <CheckCircle2
              className={`h-3.5 w-3.5 ${
                isContested ? "text-amber-400" : "text-teal-400"
              }`}
            />
          )}
        </button>

        {/* Icon */}
        {isContested ? (
          <div className="relative">
            <AlertTriangle className="h-4 w-4 text-amber-400" />
            <span className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
          </div>
        ) : (
          <CheckCircle2 className="h-4 w-4 text-teal-400" />
        )}

        {/* Label */}
        <span className="flex-1 text-sm font-medium">{step.name}</span>

        {/* Classification badge */}
        <Badge
          variant="outline"
          className={`text-xs border-0 ${
            isContested
              ? "bg-amber-500/20 text-amber-400"
              : "bg-teal-500/20 text-teal-400"
          }`}
        >
          {isContested ? "Contested" : "Standard"}
        </Badge>

        {/* Expand */}
        {(step.rationale || step.ensemble_methods) && (
          <span className="text-muted-foreground">
            {expanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </span>
        )}
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 pt-1 space-y-3">
              {step.rationale && (
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {step.rationale}
                </p>
              )}

              {step.ensemble_methods && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-amber-400">
                    Ensemble methods (all will run, results compared):
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {step.ensemble_methods.map((m) => (
                      <Badge
                        key={m.id}
                        variant="outline"
                        className="bg-card/50 text-xs"
                      >
                        <Beaker className="h-3 w-3 mr-1" />
                        {m.name}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function ClarificationPanel({ projectId }: { projectId: string }) {
  const { data: clarification, isLoading } = useQuery({
    queryKey: ["clarifications", projectId],
    queryFn: () => api.getClarifications(projectId),
  });
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [customText, setCustomText] = useState<Record<string, string>>({});

  const submitMutation = useMutation({
    mutationFn: async () => {
      const payload = (clarification?.questions || [])
        .filter((question) => (answers[question.id] ?? []).length > 0)
        .map((question) => ({ id: question.id, values: answers[question.id] }));
      await api.submitClarifications(projectId, payload);
    },
  });

  const toggleValue = (question: ClarificationQuestion, option: string) => {
    setAnswers((prev) => {
      const current = prev[question.id] ?? [];
      if (question.multiple) {
        return {
          ...prev,
          [question.id]: current.includes(option)
            ? current.filter((value) => value !== option)
            : [...current, option],
        };
      }
      return { ...prev, [question.id]: current[0] === option ? [] : [option] };
    });
  };

  const setCustom = (question: ClarificationQuestion, value: string) => {
    setCustomText((prev) => ({ ...prev, [question.id]: value }));
    setAnswers((prev) => ({
      ...prev,
      [question.id]: value.trim() ? [value.trim()] : [],
    }));
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!clarification?.questions?.length) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-20 text-center">
        <p className="text-sm text-muted-foreground">
          Waiting for the planner to continue...
        </p>
      </div>
    );
  }

  const ready = clarification.questions.every(
    (question) => (answers[question.id] ?? []).length > 0
  );

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <div className="mb-8 flex items-start gap-3">
          <HelpCircle className="mt-0.5 h-6 w-6 shrink-0 text-teal-400" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              A couple of quick decisions
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {clarification.message}
            </p>
          </div>
        </div>

        <div className="space-y-6">
          {clarification.questions.map((question) => (
            <Card key={question.id} className="border-border/50 bg-card/30">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium leading-6">
                  {question.prompt}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {question.options.map((option) => {
                  const selected = (answers[question.id] ?? []).includes(option);
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => toggleValue(question, option)}
                      className={`flex w-full items-center gap-3 rounded-xl border px-4 py-2.5 text-left text-sm transition ${
                        selected
                          ? "border-teal-500 bg-teal-500/10 text-foreground"
                          : "border-border/60 bg-background/40 text-foreground hover:bg-muted"
                      }`}
                    >
                      <span
                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded ${
                          question.multiple ? "rounded-sm" : "rounded-full"
                        } border ${
                          selected ? "border-teal-500 bg-teal-500" : "border-border"
                        }`}
                      >
                        {selected && <CheckCircle2 className="h-3 w-3 text-white" />}
                      </span>
                      <span className="text-foreground/90">{option}</span>
                    </button>
                  );
                })}
                {question.allow_custom ? (
                  <input
                    type="text"
                    value={customText[question.id] ?? ""}
                    onChange={(event) => setCustom(question, event.target.value)}
                    placeholder="Or type another value..."
                    className="mt-1 h-9 w-full rounded-md border border-border/60 bg-background px-3 text-sm text-foreground outline-none focus:border-teal-500/60"
                  />
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>

        <Button
          onClick={() => submitMutation.mutate()}
          disabled={!ready || submitMutation.isPending}
          className="mt-8 w-full gap-2 bg-gradient-to-r from-teal-600 to-cyan-600 hover:from-teal-500 hover:to-cyan-500 h-12 text-base shadow-lg shadow-teal-500/20"
        >
          {submitMutation.isPending ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <Play className="h-5 w-5" />
          )}
          Continue
        </Button>
        {submitMutation.isError ? (
          <p className="mt-3 text-center text-sm text-red-600 dark:text-red-300">
            {(submitMutation.error as Error).message}
          </p>
        ) : null}
      </motion.div>
    </div>
  );
}


export default function PlanningPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data?.status === "planning" ? 2000 : false;
    },
  });

  const [workflow, setWorkflow] = useState<WorkflowStep[] | null>(null);
  const [selectedGroupingKey, setSelectedGroupingKey] = useState("");
  const effectiveWorkflow = workflow ?? project?.analysis_plan?.workflow ?? null;
  const defaultGrouping = project?.study_manifest?.grouping_candidates.find(
    (candidate) => candidate.column === project.analysis_plan?.grouping_variable
  );
  const effectiveGroupingKey =
    selectedGroupingKey || (defaultGrouping ? `${defaultGrouping.file}::${defaultGrouping.column}` : "");

  const toggleStep = (id: string) => {
    setWorkflow(
      (effectiveWorkflow || []).map((step) =>
        step.id === id ? { ...step, enabled: !step.enabled } : step
      )
    );
  };

  const approveMutation = useMutation({
    mutationFn: async () => {
      if (!project?.analysis_plan || !effectiveWorkflow) return;
      const selectedGrouping = project.study_manifest?.grouping_candidates.find(
        (candidate) => `${candidate.file}::${candidate.column}` === effectiveGroupingKey
      );
      const updatedPlan = {
        ...project.analysis_plan,
        workflow: effectiveWorkflow,
        grouping_variable: selectedGrouping?.column ?? project.analysis_plan.grouping_variable,
        group_levels: selectedGrouping?.levels ?? project.analysis_plan.group_levels,
      };
      await api.approvePlan(projectId, updatedPlan);
      await api.startGeneration(projectId);
      router.push(`/projects/${projectId}/workspace`);
    },
  });

  const retryPlanningMutation = useMutation({
    mutationFn: async () => {
      await api.startPlanning(projectId);
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (project?.status === "needs_clarification") {
    return <ClarificationPanel projectId={projectId} />;
  }

  if (project?.status === "planning") {
    return (
      <div className="mx-auto max-w-3xl px-6 py-20 text-center">
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <Loader2 className="mx-auto h-12 w-12 animate-spin text-teal-400 mb-6" />
          <h2 className="text-xl font-semibold mb-2">Analyzing your data...</h2>
          <p className="text-sm text-muted-foreground">
            The AI is inspecting your files and designing an analysis plan.
          </p>
        </motion.div>
      </div>
    );
  }

  if (project?.status === "failed" || (!project?.analysis_plan && project?.status !== "planning")) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-20 text-center space-y-4">
        <AlertTriangle className="mx-auto h-12 w-12 text-amber-400 mb-2" />
        <h2 className="text-xl font-semibold text-foreground">Planning Encountered an Issue</h2>
        <p className="text-sm text-muted-foreground max-w-md mx-auto">
          The planning task did not complete cleanly. You can retry generating the plan using the automated workflow.
        </p>
        <Button
          onClick={() => retryPlanningMutation.mutate()}
          disabled={retryPlanningMutation.isPending}
          className="bg-teal-600 hover:bg-teal-500 gap-2"
        >
          {retryPlanningMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          Retry Analysis Planning
        </Button>
      </div>
    );
  }

  const plan = project?.analysis_plan;
  if (!plan) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-20 text-center space-y-4">
        <p className="text-muted-foreground">No analysis plan available yet.</p>
        <Button onClick={() => retryPlanningMutation.mutate()} className="bg-teal-600 hover:bg-teal-500">
          Generate Plan
        </Button>
      </div>
    );
  }

  const standardSteps = effectiveWorkflow?.filter((s) => s.classification === "standard") || [];
  const contestedSteps = effectiveWorkflow?.filter((s) => s.classification === "contested") || [];
  const manifest = project?.study_manifest;

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-bold tracking-tight mb-1">Analysis Plan</h1>
        <p className="text-sm text-muted-foreground mb-8">
          Review the proposed workflow. Contested steps will run multiple methods and compare results.
        </p>

        {/* Detected inputs */}
        <Card className="mb-6 border-border/50 bg-card/30">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <FileCheck className="h-4 w-4 text-teal-400" />
              Detected Inputs
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {plan.detected_inputs?.map((input, i) => (
              <div key={i} className="flex items-center gap-3 text-sm">
                <Badge variant="outline" className="text-xs bg-card/50">
                  {input.role}
                </Badge>
                <span className="text-muted-foreground">{input.file}</span>
                <span className="text-xs text-muted-foreground/60">{input.format}</span>
              </div>
            ))}
            {plan.grouping_variable && (
              <div className="mt-3 pt-3 border-t border-border/30">
                <p className="text-sm">
                  <span className="text-muted-foreground">Groups: </span>
                  <span className="font-medium">{plan.grouping_variable}</span>
                  {plan.group_levels?.length > 0 && (
                    <span className="text-muted-foreground">
                      {" → "}
                      {plan.group_levels.join(" vs ")}
                    </span>
                  )}
                </p>
              </div>
            )}
            {manifest?.grouping_candidates && manifest.grouping_candidates.length > 0 ? (
              <div className="mt-3 border-t border-border/30 pt-3">
                <label htmlFor="grouping-variable" className="mb-1.5 block text-xs text-muted-foreground">
                  Confirm comparison groups
                </label>
                <select
                  id="grouping-variable"
                  value={effectiveGroupingKey}
                  onChange={(event) => setSelectedGroupingKey(event.target.value)}
                  className="h-9 w-full rounded-md border border-border/60 bg-background px-3 text-sm text-foreground"
                >
                  {manifest.grouping_candidates.map((candidate) => (
                    <option
                      key={`${candidate.file}::${candidate.column}`}
                      value={`${candidate.file}::${candidate.column}`}
                    >
                      {candidate.column} ({candidate.levels.join(", ")}) — {candidate.file}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {manifest?.validations?.length ? (
          <div className="mb-6 space-y-2">
            {manifest.validations.map((validation) => (
              <div
                key={validation.code}
                className={`rounded-lg border px-3 py-2 text-xs ${
                  validation.severity === "error"
                    ? "border-red-500/20 bg-red-500/5 text-red-300"
                    : "border-amber-500/20 bg-amber-500/5 text-amber-300"
                }`}
              >
                {validation.message}
              </div>
            ))}
          </div>
        ) : null}

        {/* Workflow */}
        <div className="space-y-6">
          {/* Standard steps */}
          {standardSteps.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-teal-400" />
                Standard steps
              </h3>
              <div className="space-y-2">
                {standardSteps.map((step) => (
                  <StepItem key={step.id} step={step} onToggle={toggleStep} />
                ))}
              </div>
            </div>
          )}

          {/* Contested steps */}
          {contestedSteps.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-400" />
                Contested steps — will run as ensemble, results compared
              </h3>
              <div className="space-y-2">
                {contestedSteps.map((step) => (
                  <StepItem key={step.id} step={step} onToggle={toggleStep} />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Info note */}
        <div className="mt-6 rounded-lg border border-blue-500/20 bg-blue-500/5 p-4 flex gap-3">
          <Info className="h-4 w-4 text-blue-400 mt-0.5 shrink-0" />
          <p className="text-xs text-blue-300/80 leading-relaxed">
            Contested steps run multiple accepted methods on the same data and compare results.
            The final report will show which findings are robust across methods and which depend
            on the analytical choice — so you know whether a result holds up or not.
          </p>
        </div>

        {/* Runtime estimate */}
        {plan.estimated_runtime_minutes && (
          <p className="mt-4 text-sm text-muted-foreground">
            Estimated runtime: ~{Math.ceil(plan.estimated_runtime_minutes)} minutes
          </p>
        )}

        {/* Approve button */}
        <Separator className="my-8" />
        <Button
          onClick={() => approveMutation.mutate()}
          disabled={approveMutation.isPending}
          className="w-full bg-gradient-to-r from-teal-600 to-cyan-600 hover:from-teal-500 hover:to-cyan-500 gap-2 h-12 text-base shadow-lg shadow-teal-500/20"
        >
          {approveMutation.isPending ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <>
              <Play className="h-5 w-5" />
              Approve & Build
            </>
          )}
        </Button>
      </motion.div>
    </div>
  );
}
