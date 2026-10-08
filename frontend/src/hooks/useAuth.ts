// Re-export useAuth from AuthContext so existing imports of '@/hooks/useAuth' continue to work.
export { useAuth } from '@/contexts/AuthContext';
export type { AuthUser } from '@/contexts/AuthContext';
