import { useOperatingMode } from '@/contexts/OperatingModeContext';
import type { OperatingMode } from '@/types';

type BannerConfig = {
  label: string;
  bg: string;
  text: string;
};

const BANNER_CONFIGS: Record<OperatingMode, BannerConfig> = {
  SIMULATED: { label: 'SIMULATED', bg: 'bg-blue-600', text: 'text-white' },
  REAL_TEST_MODE: { label: 'REAL TEST MODE', bg: 'bg-amber-500', text: 'text-black' },
  DEMO_FIXTURE: { label: 'DEMO FIXTURE', bg: 'bg-violet-600', text: 'text-white' },
};

/**
 * Persistent banner shown at the top of every page.
 * Displays the current operating mode with a color-coded indicator:
 * - SIMULATED    → blue
 * - REAL_TEST_MODE → amber
 * - DEMO_FIXTURE  → violet
 * Hidden when the operating mode is not yet known.
 */
export function OperatingModeBanner() {
  const { operatingMode } = useOperatingMode();

  if (!operatingMode) return null;

  const config = BANNER_CONFIGS[operatingMode] ?? {
    label: operatingMode,
    bg: 'bg-gray-500',
    text: 'text-white',
  };

  return (
    <div
      role="banner"
      aria-label={`Operating mode: ${config.label}`}
      className={`${config.bg} ${config.text} text-center py-1 text-xs font-semibold tracking-widest uppercase select-none`}
    >
      {config.label}
    </div>
  );
}
