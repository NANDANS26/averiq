import React, { createContext, useContext, useState, useEffect } from 'react';
import { apiClient } from '@/api/client';
import type { OperatingMode } from '@/types';
import type { AxiosResponse } from 'axios';

interface OperatingModeContextValue {
  operatingMode: OperatingMode | null;
  isSimulated: boolean;
}

const OperatingModeContext = createContext<OperatingModeContextValue>({
  operatingMode: null,
  isSimulated: true,
});

export function OperatingModeProvider({ children }: { children: React.ReactNode }) {
  const [operatingMode, setOperatingMode] = useState<OperatingMode | null>(null);

  useEffect(() => {
    // Intercept any response to read the X-Operating-Mode header
    const interceptor = apiClient.interceptors.response.use((response: AxiosResponse) => {
      const mode = response.headers['x-operating-mode'] as OperatingMode | undefined;
      if (mode) setOperatingMode(mode);
      return response;
    });

    // Also fetch from /status on mount
    apiClient
      .get<{ operating_mode: OperatingMode }>('/status')
      .then((r) => {
        if (r.data.operating_mode) {
          setOperatingMode(r.data.operating_mode);
        }
      })
      .catch(() => {
        // Backend may not be running — default to null (banner hidden)
      });

    return () => apiClient.interceptors.response.eject(interceptor);
  }, []);

  return (
    <OperatingModeContext.Provider
      value={{ operatingMode, isSimulated: operatingMode === 'SIMULATED' }}
    >
      {children}
    </OperatingModeContext.Provider>
  );
}

export function useOperatingMode() {
  return useContext(OperatingModeContext);
}
