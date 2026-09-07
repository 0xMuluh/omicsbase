import React from 'react';
import type { UseFormReturn } from 'react-hook-form';
import { Plus } from 'lucide-react';
import type { ChatFormValues } from '~/common';
import { useChatContext } from '~/Providers';
import { noteCellsApi } from '~/data-provider/Notes/notesCellsApi';

export default function AddRCellButton({
  methods,
  disabled = false,
}: {
  methods: Pick<UseFormReturn<ChatFormValues>, 'getValues' | 'setValue'>;
  disabled?: boolean;
}) {
  const { conversation } = useChatContext();
  const conversationId = conversation?.conversationId;

  const handleInsertRCell = () => {
    const currentText = methods.getValues('text') || '';
    const blockKey = `composer-${Date.now()}`;
    const rSnippet = '```r\n# R NoteKernel cell\n\n```';
    const newText = currentText.trim()
      ? `${currentText.trim()}\n\n${rSnippet}`
      : rSnippet;

    methods.setValue('text', newText, { shouldValidate: true, shouldDirty: true });

    if (conversationId && conversationId !== 'new') {
      void noteCellsApi
        .createOrFindCell(conversationId, {
          content: '# R NoteKernel cell\n',
          blockKey,
          messageId: null,
        })
        .catch(() => {
          /* non-blocking; cell binds on render */
        });
    }

    const textarea = document.querySelector('textarea[name="text"]') as HTMLTextAreaElement | null;
    if (textarea) {
      textarea.focus();
      const targetPos =
        newText.indexOf('\n\n```') !== -1 ? newText.indexOf('\n\n```') + 1 : newText.length;
      textarea.setSelectionRange(targetPos, targetPos);
    }
  };

  return (
    <button
      type="button"
      onClick={handleInsertRCell}
      disabled={disabled}
      className="inline-flex items-center gap-1 rounded-lg border border-accent-primary/20 bg-accent-primary/10 px-2 py-1 text-xs font-medium text-accent-primary transition-colors hover:bg-accent-primary/20 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
      title="Insert executable R Notebook Cell"
    >
      <Plus className="size-3" />
      <span className="font-mono font-semibold">R Cell</span>
    </button>
  );
}
