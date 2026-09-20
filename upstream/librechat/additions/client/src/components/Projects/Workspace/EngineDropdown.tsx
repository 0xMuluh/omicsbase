import React from 'react';
import { TooltipAnchor } from '@librechat/client';
import { cn } from '~/utils';
import { AgentEngineKey } from './types';
import { AGENT_ENGINES } from './engines';

interface EngineDropdownProps {
  selectedEngine: AgentEngineKey;
  isOpen: boolean;
  setIsOpen: React.Dispatch<React.SetStateAction<boolean>>;
  onSelectEngine: (engineKey: AgentEngineKey) => void;
  isLaunching: boolean;
}

export default function EngineDropdown({
  selectedEngine,
  isOpen,
  setIsOpen,
  onSelectEngine,
  isLaunching,
}: EngineDropdownProps) {
  const currentEngine = AGENT_ENGINES.find((e) => e.key === selectedEngine) || AGENT_ENGINES[0];
  const CurrentIcon = currentEngine.icon;

  return (
    <div className="relative">
      <TooltipAnchor
        description={`Agent: ${currentEngine.name}`}
        render={
          <button
            type="button"
            onClick={() => setIsOpen((prev) => !prev)}
            className={cn(
              'flex h-7 items-center gap-1 rounded-lg border border-border-light bg-surface-secondary px-1.5 text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary shadow-xs',
              isOpen && 'border-primary/40 bg-surface-hover text-text-primary',
            )}
            aria-label={`Agent: ${currentEngine.name}`}
          >
            <CurrentIcon size={15} className="shrink-0 text-primary" />
            <span className="text-[9px] text-text-tertiary">▾</span>
          </button>
        }
      />

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute right-0 top-full mt-1.5 z-50 w-56 rounded-xl border border-border-light bg-surface-primary p-1.5 shadow-xl">
            <div className="px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-text-tertiary">
              Switch Agent Engine
            </div>
            <div className="space-y-1">
              {AGENT_ENGINES.map((engine) => {
                const Icon = engine.icon;
                const isActive = selectedEngine === engine.key;
                return (
                  <button
                    key={engine.key}
                    type="button"
                    disabled={isLaunching}
                    onClick={() => {
                      setIsOpen(false);
                      onSelectEngine(engine.key);
                    }}
                    className={cn(
                      'w-full flex items-center gap-2.5 rounded-lg p-2 text-left transition-colors',
                      isActive
                        ? 'bg-surface-secondary text-text-primary font-medium'
                        : 'hover:bg-surface-hover text-text-secondary hover:text-text-primary',
                    )}
                  >
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-border-light bg-surface-primary shrink-0 text-text-primary">
                      <Icon size={16} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-medium text-text-primary">
                        {engine.name}
                      </div>
                      <p className="text-[10px] text-text-tertiary mt-0.5">
                        {engine.provider}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
