import React from 'react';
import { History, CheckCircle2, Plus } from 'lucide-react';
import { TooltipAnchor } from '@librechat/client';
import { cn } from '~/utils';
import { ProjectSessionOption } from './types';

interface ThreadDropdownProps {
  conversationId: string | null;
  projectConversations: ProjectSessionOption[];
  isOpen: boolean;
  setIsOpen: React.Dispatch<React.SetStateAction<boolean>>;
  onSelectConversation: (conversationId: string) => void;
  onNewThread: () => void;
  isLaunching: boolean;
}

export default function ThreadDropdown({
  conversationId,
  projectConversations,
  isOpen,
  setIsOpen,
  onSelectConversation,
  onNewThread,
  isLaunching,
}: ThreadDropdownProps) {
  const activeSession = projectConversations.find((c) => c.conversation_id === conversationId);
  const activeTitle =
    activeSession?.title ||
    (conversationId ? `Conversation ${conversationId.slice(0, 5)}` : 'Select Thread');

  return (
    <div className="relative">
      <TooltipAnchor
        description="Switch or view agent threads"
        render={
          <button
            type="button"
            onClick={() => setIsOpen((prev) => !prev)}
            className={cn(
              'flex h-7 items-center gap-1.5 rounded-lg border border-border-light bg-surface-secondary px-2 text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary shadow-xs',
              isOpen && 'border-primary/40 bg-surface-hover text-text-primary',
            )}
            aria-label="Switch agent thread"
          >
            <History size={13} className="shrink-0 text-text-secondary" />
            <span className="truncate max-w-[100px] sm:max-w-[150px] text-xs font-medium text-text-primary">
              {activeTitle}
            </span>
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
          <div className="absolute right-0 top-full mt-1.5 z-50 w-72 rounded-xl border border-border-light bg-surface-primary p-1.5 shadow-xl">
            <div className="flex items-center justify-between px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-text-tertiary">
              <span>Agent Threads</span>
              <span className="text-[10px] font-normal text-text-tertiary">
                {projectConversations.length} total
              </span>
            </div>

            <div className="max-h-60 overflow-y-auto space-y-1">
              {projectConversations.length === 0 ? (
                <div className="px-3 py-4 text-center text-xs text-text-tertiary">
                  No threads found for this project
                </div>
              ) : (
                projectConversations.map((convo) => {
                  const isActive = convo.conversation_id === conversationId;
                  return (
                    <button
                      key={convo.conversation_id}
                      type="button"
                      onClick={() => {
                        setIsOpen(false);
                        onSelectConversation(convo.conversation_id);
                      }}
                      className={cn(
                        'w-full flex items-center justify-between gap-2 rounded-lg p-2 text-left transition-colors',
                        isActive
                          ? 'bg-surface-secondary text-text-primary font-medium'
                          : 'hover:bg-surface-hover text-text-secondary hover:text-text-primary',
                      )}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium text-text-primary">
                          {convo.title}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5 text-[10px] text-text-tertiary">
                          <span className="uppercase text-[9px] font-semibold tracking-wider text-text-tertiary">
                            {convo.agent_engine || 'openhands'}
                          </span>
                          {convo.event_count > 0 ? (
                            <>
                              <span>•</span>
                              <span className="text-emerald-500 font-medium">{convo.event_count} events</span>
                            </>
                          ) : (
                            <>
                              <span>•</span>
                              <span className="text-text-tertiary">New</span>
                            </>
                          )}
                        </div>
                      </div>
                      {isActive && <CheckCircle2 className="h-3.5 w-3.5 text-primary shrink-0" />}
                    </button>
                  );
                })
              )}
            </div>

            <div className="mt-1 border-t border-border-light pt-1">
              <button
                type="button"
                disabled={isLaunching}
                onClick={() => {
                  setIsOpen(false);
                  onNewThread();
                }}
                className="w-full flex items-center gap-2 rounded-lg p-2 text-left text-xs font-medium text-primary hover:bg-surface-hover transition-colors"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>New Agent Thread</span>
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
