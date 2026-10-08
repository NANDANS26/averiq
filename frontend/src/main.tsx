import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider, QueryCache, MutationCache } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './index.css';

/**
 * Global API error logger (FE-009).
 * In development, logs structured API errors to the console.
 * Components surface their own structured error state + retry option.
 */
function handleGlobalError(error: unknown) {
  if (import.meta.env.DEV) {
    console.error('[Averiq API error]', error);
  }
}

// Configure TanStack Query client with sensible defaults for Averiq
const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: handleGlobalError,
  }),
  mutationCache: new MutationCache({
    onError: handleGlobalError,
  }),
  defaultOptions: {
    queries: {
      // Stale time: 30 seconds — cases can change frequently
      staleTime: 30_000,
      // Retry failed requests up to 2 times (not for 401/403/404)
      retry: (failureCount, error) => {
        if (error instanceof Error && 'status' in error) {
          const status = (error as { status: number }).status;
          if (status === 401 || status === 403 || status === 404) return false;
        }
        return failureCount < 2;
      },
      // Refetch on window focus for live queue updates
      refetchOnWindowFocus: true,
    },
    mutations: {
      // Do not retry mutations by default (financial safety)
      retry: false,
    },
  },
});

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found in index.html');
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/*
          AuthProvider and OperatingModeProvider are mounted inside App
          so they have access to the router context (for redirects) and
          can leverage QueryClient via useQuery. This is the standard
          pattern for React Router v6 + TanStack Query v5.
        */}
        <App />
      </BrowserRouter>
      {/* DevTools only rendered in development */}
      {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  </React.StrictMode>,
);
