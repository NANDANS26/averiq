import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { OperatingModeBanner } from './OperatingModeBanner';
import { useAuth } from '@/contexts/AuthContext';

interface NavItem {
  label: string;
  path: string;
  badge?: string;
}

const ADMIN_NAV: NavItem[] = [
  { label: 'Overview', path: '/' },
  { label: 'Risk Queue', path: '/queue' },
  { label: 'Operations', path: '/operations' },
  { label: 'Analytics', path: '/analytics' },
  { label: 'Policy Center', path: '/policy' },
  { label: 'System Admin', path: '/system' },
  { label: 'Demo Center', path: '/demo' },
];

const OPERATOR_NAV: NavItem[] = [
  { label: 'Overview', path: '/' },
  { label: 'Risk Queue', path: '/queue' },
  { label: 'Operations Center', path: '/operations' },
  { label: 'Recovery Analytics', path: '/analytics' },
  { label: 'Demo Center', path: '/demo' },
];

const VIEWER_NAV: NavItem[] = [
  { label: 'Overview', path: '/' },
  { label: 'Analytics', path: '/analytics' },
];

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const navItems = user?.role === 'ADMIN'
    ? ADMIN_NAV
    : user?.role === 'OPERATOR'
      ? OPERATOR_NAV
      : VIEWER_NAV;

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100 selection:bg-indigo-500 selection:text-white">
      {/* Top Operating Mode Banner */}
      <OperatingModeBanner />

      {/* Primary Executive Navigation Header */}
      <header className="sticky top-0 z-40 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between">
          <div className="flex items-center gap-8">
            {/* Brand Logo & Pipeline Tag */}
            <div 
              onClick={() => navigate('/')} 
              className="flex items-center gap-2.5 cursor-pointer group"
            >
              <div className="w-7 h-7 rounded-md bg-indigo-600 flex items-center justify-center font-bold text-white shadow-sm shadow-indigo-500/20 group-hover:bg-indigo-500 transition-colors">
                AQ
              </div>
              <div className="flex flex-col">
                <span className="font-semibold text-sm tracking-tight text-white leading-tight">
                  AVERIQ
                </span>
                <span className="text-[10px] text-slate-400 font-mono tracking-wider uppercase leading-none">
                  {user?.role === 'ADMIN' ? 'Control Plane' : 'Decision Engine'}
                </span>
              </div>
            </div>

            {/* Main Tabs */}
            <nav className="flex items-center gap-1">
              {navItems.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  end={item.path === '/'}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                      isActive
                        ? 'bg-slate-800 text-white shadow-sm'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>

          {/* Right Status / User Context */}
          <div className="flex items-center gap-3">
            <div className="hidden sm:flex items-center gap-2 px-2.5 py-1 rounded border border-slate-800 bg-slate-900/60 text-[11px] text-slate-300">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              <span className="font-mono text-slate-400">P0 Fallback:</span>
              <span className="font-semibold text-slate-200">0.35</span>
            </div>

            {user && (
              <div className="flex items-center gap-2.5 text-xs text-slate-400">
                {/* Role-Aware Badge */}
                <div className="flex items-center gap-1.5">
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono uppercase font-bold tracking-wider ${
                      user.role === 'ADMIN'
                        ? 'bg-violet-950 text-violet-300 border border-violet-800/80 shadow-xs'
                        : user.role === 'OPERATOR'
                          ? 'bg-emerald-950 text-emerald-300 border border-emerald-800/80 shadow-xs'
                          : 'bg-slate-800 text-slate-300 border border-slate-700'
                    }`}
                  >
                    {user.role === 'ADMIN' ? 'Admin' : user.role === 'OPERATOR' ? 'Operator' : 'Viewer'}
                  </span>
                </div>

                <span className="hidden md:inline font-mono text-[11px] text-slate-400">
                  {user.email}
                </span>

                <button
                  onClick={logout}
                  className="px-2.5 py-1 rounded border border-slate-800 hover:border-slate-700 bg-slate-900 text-slate-300 hover:text-white transition-colors text-[11px]"
                >
                  Logout
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Main View Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <Outlet />
      </main>

      {/* Compact Engineering Footer */}
      <footer className="border-t border-slate-900 bg-slate-950 py-3 text-center text-[11px] text-slate-500 font-mono">
        Averiq v0.1.0 • Calibrated T-Learner LightGBM • Integer-Paise Canonical Economics • Redis & PostgreSQL 18
      </footer>
    </div>
  );
}
