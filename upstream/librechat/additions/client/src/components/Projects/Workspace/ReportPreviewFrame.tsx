import React from 'react';
import { FileCode, Play } from 'lucide-react';
import { Button, Spinner } from '@librechat/client';

interface ReportPreviewFrameProps {
  hasSite: boolean;
  quartoSiteUrl: string;
  previewKey: number;
  projectName: string;
  isRendering: boolean;
  onRenderQuarto: () => void;
  onReturnToAgent: () => void;
}

export default function ReportPreviewFrame({
  hasSite,
  quartoSiteUrl,
  previewKey,
  projectName,
  isRendering,
  onRenderQuarto,
  onReturnToAgent,
}: ReportPreviewFrameProps) {
  if (hasSite) {
    return (
      <div className="relative h-full w-full">
        <iframe
          key={previewKey}
          src={`${quartoSiteUrl}?v=${previewKey}`}
          title="Quarto Live Report Preview"
          className="h-full w-full border-none bg-white"
        />
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col items-center justify-center p-6 text-center">
      <div className="w-full max-w-md rounded-2xl border border-border-light bg-surface-primary p-6 shadow-sm">
        <FileCode className="mx-auto h-12 w-12 text-text-tertiary" />
        <h3 className="mt-3 text-base font-medium text-text-primary">
          Quarto Report Not Rendered Yet
        </h3>
        <p className="mt-1 text-xs text-text-secondary">
          The publication website has not been compiled yet for study '{projectName}'.
        </p>
        <div className="mt-5 flex justify-center gap-3">
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={onRenderQuarto}
            disabled={isRendering}
            className="gap-2 text-xs"
          >
            {isRendering ? (
              <Spinner className="h-3.5 w-3.5 text-white" />
            ) : (
              <Play className="h-3.5 w-3.5 fill-white" />
            )}
            <span>Compile Report Now</span>
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onReturnToAgent}
            className="text-xs"
          >
            Return to Agent Canvas
          </Button>
        </div>
      </div>
    </div>
  );
}
