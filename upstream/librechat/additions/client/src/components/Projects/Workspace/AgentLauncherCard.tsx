import React from 'react';
import { Layers, Play } from 'lucide-react';
import { Button, Spinner } from '@librechat/client';
import { cn } from '~/utils';
import { AgentEngineKey } from './types';
import { AGENT_ENGINES } from './engines';

interface AgentLauncherCardProps {
  projectId: string;
  selectedEngine: AgentEngineKey;
  setSelectedEngine: (engine: AgentEngineKey) => void;
  onLaunchAgent: () => void;
  isLaunching: boolean;
  workspaceAuthorized: boolean;
}

export default function AgentLauncherCard({
  projectId,
  selectedEngine,
  setSelectedEngine,
  onLaunchAgent,
  isLaunching,
  workspaceAuthorized,
}: AgentLauncherCardProps) {
  const currentEngine = AGENT_ENGINES.find((e) => e.key === selectedEngine) || AGENT_ENGINES[0];

  return (
    <div className="flex h-full w-full flex-col items-center justify-center p-6 text-center">
      <div className="w-full max-w-2xl rounded-2xl border border-border-light bg-surface-primary p-6 text-left shadow-md">
        <div>
          <h2 className="text-base font-semibold text-text-primary">
            Downstream Omics Workspace Agent
          </h2>
          <p className="text-xs text-text-secondary">
            Multi-file Quarto report authoring, live code steering, and Bioconductor workflow execution.
          </p>
        </div>

        <div className="mt-5 space-y-4">
          <div className="rounded-xl border border-border-light bg-surface-secondary/60 p-3 text-xs text-text-secondary">
            <div className="flex items-center gap-2 font-medium text-text-primary">
              <Layers className="text-primary h-3.5 w-3.5" />
              <span>Workspace Target: ./projects/{projectId}</span>
            </div>
            <p className="mt-1 leading-relaxed">
              The Workspace Agent directly inspects, edits, and compiles files in your report directory. You can steer the agent in a note, audit R code in Monaco, and review line-by-line Git diffs.
            </p>
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                Agent Execution Engine
              </span>
              <span className="text-[11px] text-text-tertiary">
                Choose agent runtime for this session
              </span>
            </div>

            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              {AGENT_ENGINES.map((engine) => {
                const Icon = engine.icon;
                const isSelected = selectedEngine === engine.key;
                return (
                  <button
                    key={engine.key}
                    type="button"
                    onClick={() => setSelectedEngine(engine.key)}
                    className={cn(
                      'group relative flex flex-col justify-between rounded-xl border p-3 text-left transition-all',
                      isSelected
                        ? 'border-primary bg-surface-secondary shadow-xs ring-2 ring-primary/20'
                        : 'border-border-light bg-surface-secondary/40 hover:border-border-medium hover:bg-surface-secondary/70',
                    )}
                  >
                    <div>
                      <div className="flex items-center gap-2.5">
                        <div
                          className={cn(
                            'flex h-7 w-7 items-center justify-center rounded-lg border transition-colors',
                            isSelected
                              ? 'border-primary/40 bg-primary/10 text-primary'
                              : 'border-border-light bg-surface-tertiary text-text-secondary group-hover:text-text-primary',
                          )}
                        >
                          <Icon size={16} className="text-text-primary" />
                        </div>
                        <div>
                          <h3 className="text-xs font-semibold text-text-primary">
                            {engine.name}
                          </h3>
                          <p className="text-[10px] text-text-tertiary">
                            {engine.provider}
                          </p>
                        </div>
                      </div>
                    </div>

                    <div className="mt-2.5 flex items-center gap-1.5 pt-1 text-[10px] font-medium text-text-tertiary">
                      <span
                        className={cn(
                          'h-1.5 w-1.5 rounded-full',
                          isSelected ? 'bg-primary' : 'bg-border-medium',
                        )}
                      />
                      <span>{isSelected ? 'Selected' : 'Click to select'}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="mt-5 flex items-center justify-between border-t border-border-light pt-4">
          <div className="text-xs text-text-tertiary">
            Engine: <span className="font-semibold text-text-primary">{currentEngine.name}</span>
          </div>
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={onLaunchAgent}
            disabled={isLaunching || !workspaceAuthorized}
            className="gap-2 px-4 py-2 text-xs font-medium"
          >
            {isLaunching ? (
              <Spinner className="h-4 w-4 text-white" />
            ) : (
              <Play className="h-4 w-4 fill-white" />
            )}
            <span>{isLaunching ? 'Initializing Environment...' : 'Open Workspace'}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
