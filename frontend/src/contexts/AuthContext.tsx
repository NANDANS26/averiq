import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { apiClient } from '@/api/client';
import type { UserRole } from '@/types';

export interface AuthUser {
  id: string;
  email: string;
  role: UserRole;
  merchantId: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  merchantId: string | null;
  role: UserRole | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  error: string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('averiq_access_token'));
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const login = useCallback(async (email: string, password: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiClient.post('/auth/login', { email, password });
      const { access_token } = response.data as { access_token: string };
      localStorage.setItem('averiq_access_token', access_token);
      setToken(access_token);
      // Decode JWT payload for user info
      const payload = JSON.parse(atob(access_token.split('.')[1])) as {
        sub: string;
        role: UserRole;
        merchant_id: string;
      };
      setUser({ id: payload.sub, email, role: payload.role, merchantId: payload.merchant_id });
    } catch (err: unknown) {
      const apiErr = err as { error?: string };
      const message = apiErr.error ?? 'Login failed';
      setError(message);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiClient.post('/auth/logout');
    } catch {
      // Ignore logout errors — clear client-side state regardless
    }
    localStorage.removeItem('averiq_access_token');
    setToken(null);
    setUser(null);
  }, []);

  // Hydrate user from stored token on mount
  useEffect(() => {
    if (token && !user) {
      try {
        const payload = JSON.parse(atob(token.split('.')[1])) as {
          sub: string;
          role: UserRole;
          merchant_id: string;
          exp: number;
        };
        if (payload.exp * 1000 > Date.now()) {
          setUser({
            id: payload.sub,
            email: '',
            role: payload.role,
            merchantId: payload.merchant_id,
          });
        } else {
          // Token expired — clear it
          localStorage.removeItem('averiq_access_token');
          setToken(null);
        }
      } catch {
        // Malformed token — clear it
        setToken(null);
      }
    }
  }, [token, user]);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        merchantId: user?.merchantId ?? null,
        role: user?.role ?? null,
        isAuthenticated: !!user,
        isLoading,
        login,
        logout,
        error,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
