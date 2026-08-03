"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState } from "react";

/**
 * next-themes injects an inline <script> to avoid theme FOUC.
 * React 19 warns when that script is rendered from a client component.
 * Keep a real script on the server (FOUC prevention); on the client pass
 * type="application/json" so React does not treat it as an executable script.
 * @see https://github.com/pacocoursey/next-themes/issues/385
 */
const themeScriptProps =
  typeof window === "undefined"
    ? undefined
    : ({ type: "application/json" } as const);

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5 * 1000,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      storageKey="omicsbase.theme"
      scriptProps={themeScriptProps}
    >
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ThemeProvider>
  );
}
