import React, { useRef, useState, useCallback, useMemo } from 'react';
import type { UseFormReturn } from 'react-hook-form';
import * as Ariakit from '@ariakit/react';
import { Plus, TerminalSquare } from 'lucide-react';
import { FileUpload, TooltipAnchor, DropdownPopup, AttachmentIcon } from '@librechat/client';
import type { TConversation } from 'librechat-data-provider';
import type { ChatFormValues, ExtendedFile, FileSetter, MenuItemProps } from '~/common';
import { useFileHandlingNoChatContext, useLocalize } from '~/hooks';
import { useShortcutAriaKey, useShortcutHint } from '~/hooks/useKeyboardShortcuts';
import { useChatContext } from '~/Providers';
import { noteCellsApi } from '~/data-provider/Notes/notesCellsApi';
import { cn } from '~/utils';

interface ComposerPlusMenuProps {
  methods: Pick<UseFormReturn<ChatFormValues>, 'getValues' | 'setValue'>;
  conversation: TConversation | null;
  disableInputs: boolean;
  files: Map<string, ExtendedFile>;
  setFiles: FileSetter;
  setFilesLoading: React.Dispatch<React.SetStateAction<boolean>>;
}

export default function ComposerPlusMenu({
  methods,
  conversation,
  disableInputs,
  files,
  setFiles,
  setFilesLoading,
}: ComposerPlusMenuProps) {
  const localize = useLocalize();
  const [isOpen, setIsOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { conversation: chatConvo } = useChatContext();
  const activeConvo = conversation ?? chatConvo;
  const conversationId = activeConvo?.conversationId;

  const uploadFileAriaKey = useShortcutAriaKey('uploadFile');
  const uploadFileTooltip = useShortcutHint('uploadFile', localize('com_sidepanel_attach_files'));

  const { handleFileChange } = useFileHandlingNoChatContext(undefined, {
    files,
    setFiles,
    setFilesLoading,
    conversation: activeConvo,
  });

  const handleInsertRCell = useCallback(() => {
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
  }, [methods, conversationId]);

  const handleUploadClick = useCallback(() => {
    if (!inputRef.current) {
      return;
    }
    inputRef.current.value = '';
    inputRef.current.click();
  }, []);

  const dropdownItems = useMemo(() => {
    const items: MenuItemProps[] = [
      {
        label: localize('com_sidepanel_attach_files'),
        onClick: handleUploadClick,
        icon: <AttachmentIcon className="size-4" />,
        ariaLabel: localize('com_sidepanel_attach_files'),
        render: (props) => (
          <div {...props} title={uploadFileTooltip} />
        ),
      },
      {
        label: 'Add R cell',
        onClick: handleInsertRCell,
        icon: <TerminalSquare className="size-4 text-accent-primary" />,
        ariaLabel: 'Add R cell',
        render: (props) => (
          <div {...props} title="Insert executable R NoteKernel cell block" />
        ),
      },
    ];
    return items;
  }, [handleUploadClick, handleInsertRCell, localize, uploadFileTooltip]);

  const menuTrigger = (
    <TooltipAnchor
      render={
        <Ariakit.MenuButton
          disabled={disableInputs}
          id="composer-plus-menu-button"
          aria-label="Add attachments or blocks"
          aria-keyshortcuts={uploadFileAriaKey}
          className={cn(
            'flex size-theme-control items-center justify-center rounded-theme-control-round p-1 transition-all duration-theme-fast',
            'text-text-secondary hover:bg-surface-composer-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary focus-visible:ring-opacity-50',
            isOpen && 'bg-surface-composer-hover text-text-primary scale-105',
          )}
        >
          <div className="flex w-full items-center justify-center">
            <Plus className="size-5 transition-transform duration-200 ease-out" />
          </div>
        </Ariakit.MenuButton>
      }
      id="composer-plus-menu-button"
      description="Add content or R cells"
      disabled={disableInputs}
    />
  );

  return (
    <FileUpload ref={inputRef} handleFileChange={(e) => handleFileChange(e, undefined)}>
      <DropdownPopup
        menuId="composer-plus-menu"
        className="min-w-[180px] overflow-visible rounded-xl shadow-lg border border-border-light bg-surface-primary p-1.5"
        isOpen={isOpen}
        setIsOpen={setIsOpen}
        modal={false}
        portal={true}
        unmountOnHide={true}
        trigger={menuTrigger}
        items={dropdownItems}
        iconClassName="mr-2"
        itemClassName="flex items-center gap-2 px-3 py-2 text-sm rounded-lg cursor-pointer transition-colors hover:bg-surface-hover active:bg-surface-tertiary"
      />
    </FileUpload>
  );
}
