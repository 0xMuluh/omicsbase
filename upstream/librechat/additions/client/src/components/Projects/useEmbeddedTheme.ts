import { useCallback, useEffect } from 'react';
import type { RefObject } from 'react';

/** Soft Daisy semantic tokens forwarded into the OpenHands iframe. */
const themeTokens = [
  'presentation',
  'surface-primary',
  'surface-primary-alt',
  'surface-secondary',
  'surface-tertiary',
  'surface-hover',
  'surface-dialog',
  'surface-chat',
  'text-primary',
  'text-secondary',
  'text-tertiary',
  'border-light',
  'border-medium',
  'accent-primary',
  'accent-primary-hover',
  'ring-primary',
  'status-success',
] as const;

function resolveToken(styles: CSSStyleDeclaration, token: string): string | null {
  const value = styles.getPropertyValue(`--${token}`).trim();
  if (!value) return null;
  if (CSS.supports('color', value)) return value;
  const rgb = `rgb(${value})`;
  return CSS.supports('color', rgb) ? rgb : null;
}

/** Bridges resolved Soft Daisy theme tokens across the OpenHands iframe origin. */
export default function useEmbeddedTheme(frame: RefObject<HTMLIFrameElement>, origin: string) {
  const sendTheme = useCallback(() => {
    const root = document.documentElement;
    const styles = getComputedStyle(root);
    const colors: Record<string, string> = {};
    for (const token of themeTokens) {
      const resolved = resolveToken(styles, token);
      if (resolved) colors[token] = resolved;
    }
    // Need a minimum surface/text set to apply a coherent theme.
    if (!colors.presentation || !colors['text-primary'] || !colors['surface-primary']) {
      return;
    }
    frame.current?.contentWindow?.postMessage(
      {
        type: 'omicsbase:theme',
        mode: root.classList.contains('dark') ? 'dark' : 'light',
        colors,
      },
      origin,
    );
  }, [frame, origin]);

  useEffect(() => {
    const observer = new MutationObserver(sendTheme);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'style', 'data-theme'],
    });
    const onReady = (event: MessageEvent) => {
      if (
        event.origin === origin &&
        event.source === frame.current?.contentWindow &&
        event.data?.type === 'omicsbase:theme-ready'
      ) {
        sendTheme();
      }
    };
    window.addEventListener('message', onReady);
    sendTheme();
    return () => {
      observer.disconnect();
      window.removeEventListener('message', onReady);
    };
  }, [frame, origin, sendTheme]);

  return sendTheme;
}
