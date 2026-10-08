import { type ReactNode, Suspense, lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { OperatingModeProvider } from '@/contexts/OperatingModeContext';
import { AppLayout } from '@/components/AppLayout';

// Lazy-loaded page components
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const OverviewDashboard = lazy(() => import('@/pages/OverviewDashboard'));
const RevenueRiskQueue = lazy(() => import('@/pages/RevenueRiskQueue'));
const CaseIntelligence = lazy(() => import('@/pages/CaseIntelligence'));
const OperationsCenter = lazy(() => import('@/pages/OperationsCenter'));
const RecoveryAnalytics = lazy(() => import('@/pages/RecoveryAnalytics'));
const SystemHealth = lazy(() => import('@/pages/SystemHealth'));
const DemoCenter = lazy(() => import('@/pages/DemoCenter'));

// Backward compatibility legacy routes
const PolicyCenter = lazy(() => import('@/pages/PolicyCenter'));
const WebhookMonitor = lazy(() => import('@/pages/WebhookMonitor'));
const Settings = lazy(() => import('@/pages/Settings'));
const FailureLab = lazy(() => import('@/pages/FailureLab'));

function PageLoader() {
  return (
    <div className="flex h-screen items-center justify-center bg-slate-950">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
    </div>
  );
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Suspense fallback={<PageLoader />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          {/* Executive & Core Surfaces */}
          <Route path="/" element={<OverviewDashboard />} />
          <Route path="/queue" element={<RevenueRiskQueue />} />
          <Route path="/cases/:caseId" element={<CaseIntelligence />} />
          <Route path="/operations" element={<OperationsCenter />} />
          <Route path="/analytics" element={<RecoveryAnalytics />} />
          <Route path="/system" element={<SystemHealth />} />
          <Route path="/demo" element={<DemoCenter />} />

          {/* Legacy / Specialized Routes */}
          <Route path="/policy" element={<PolicyCenter />} />
          <Route path="/webhooks" element={<WebhookMonitor />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/failure-lab" element={<FailureLab />} />
          <Route path="/review" element={<Navigate to="/operations" replace />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <OperatingModeProvider>
        <AppRoutes />
      </OperatingModeProvider>
    </AuthProvider>
  );
}
