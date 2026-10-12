import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';

/**
 * LoginPage — JWT authentication entry point for Averiq.
 * Calls POST /api/v1/auth/login via AuthContext.login().
 * On success, navigates to the Overview Dashboard.
 * Displays structured error state on failure with a retry option.
 */
export default function LoginPage() {
  const { login, isLoading, error } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [demoNotice, setDemoNotice] = useState<string | null>(null);

  function validate(): boolean {
    const errors: { email?: string; password?: string } = {};
    if (!email.trim()) {
      errors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      errors.email = 'Enter a valid email address';
    }
    if (!password) {
      errors.password = 'Password is required';
    } else if (password.length < 6) {
      errors.password = 'Password must be at least 6 characters';
    }
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!validate()) return;

    try {
      await login(email, password);
      navigate('/', { replace: true });
    } catch {
      // Error is already captured in AuthContext.error state — nothing to do here
    }
  }

  function handleFillDemo() {
    const demoPwd = import.meta.env.VITE_DEMO_PASSWORD;
    if (!demoPwd) {
      setDemoNotice('Demo password not configured (VITE_DEMO_PASSWORD missing).');
      return;
    }
    setEmail('demo@averiq.io');
    setPassword(demoPwd);
    setFieldErrors({});
    setDemoNotice('Demo credentials populated. Click "Sign in" below to authenticate.');
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-8 shadow-sm">
        {/* Logo / title */}
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-semibold text-foreground">Averiq</h1>
          <p className="mt-1 text-sm text-muted-foreground">AI Revenue Recovery Decision Engine</p>
        </div>

        {/* Try Demo quick access block */}
        <div className="mb-6 rounded-lg border border-indigo-500/30 bg-indigo-500/10 p-3.5 text-center">
          <p className="text-xs font-medium text-indigo-400">Jury & Evaluation Preview</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Populate test credentials for the interactive Demo Center
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleFillDemo}
            disabled={isLoading}
            className="mt-2.5 w-full border-indigo-500/40 text-xs font-medium hover:bg-indigo-500/20"
          >
            Try Demo (Operator)
          </Button>
          {demoNotice && (
            <p
              className={`mt-2 text-xs font-medium ${
                demoNotice.includes('not configured') ? 'text-amber-400' : 'text-emerald-400'
              }`}
            >
              {demoNotice}
            </p>
          )}
        </div>

        {/* Global API error */}
        {error && (
          <div
            role="alert"
            className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
          >
            <p className="font-medium">Sign-in failed</p>
            <p className="mt-0.5">{error}</p>
          </div>
        )}

        <form onSubmit={(e) => void handleSubmit(e)} noValidate aria-label="Sign in form">
          {/* Email field */}
          <div className="mb-4">
            <label htmlFor="email" className="mb-1.5 block text-sm font-medium text-foreground">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setDemoNotice(null);
              }}
              disabled={isLoading}
              aria-describedby={fieldErrors.email ? 'email-error' : undefined}
              aria-invalid={!!fieldErrors.email}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              placeholder="operator@example.com"
            />
            {fieldErrors.email && (
              <p id="email-error" className="mt-1 text-xs text-destructive" role="alert">
                {fieldErrors.email}
              </p>
            )}
          </div>

          {/* Password field */}
          <div className="mb-6">
            <label htmlFor="password" className="mb-1.5 block text-sm font-medium text-foreground">
              Password
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setDemoNotice(null);
              }}
              disabled={isLoading}
              aria-describedby={fieldErrors.password ? 'password-error' : undefined}
              aria-invalid={!!fieldErrors.password}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              placeholder="••••••••"
            />
            {fieldErrors.password && (
              <p id="password-error" className="mt-1 text-xs text-destructive" role="alert">
                {fieldErrors.password}
              </p>
            )}
          </div>

          {/* Submit */}
          <Button
            type="submit"
            className="w-full"
            disabled={isLoading}
            aria-busy={isLoading}
          >
            {isLoading ? (
              <span className="flex items-center gap-2">
                <span
                  className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
                  aria-hidden="true"
                />
                Signing in…
              </span>
            ) : (
              'Sign in'
            )}
          </Button>
        </form>
      </div>
    </div>
  );
}
