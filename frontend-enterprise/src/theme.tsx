import { MoonOutlined, SunOutlined } from './icons';
import { Button } from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';

export type ThemeMode = 'system' | 'light' | 'dark';
export type EffectiveTheme = 'light' | 'dark';

const STORAGE_KEY = 'ultrarag_theme_mode';
const CHANGE_EVENT = 'ultrarag-theme-change';
const ORDER: ThemeMode[] = ['system', 'light', 'dark'];
let themeSwitchTimer: number | undefined;

function getStoredTheme(): ThemeMode {
  if (typeof window === 'undefined') return 'system';
  const value = window.localStorage.getItem(STORAGE_KEY);
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
}

function systemTheme(): EffectiveTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function resolveTheme(mode: ThemeMode): EffectiveTheme {
  return mode === 'system' ? systemTheme() : mode;
}

function applyTheme(mode: ThemeMode, effective: EffectiveTheme, animate = false) {
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(effective);
  root.setAttribute('data-theme', effective);
  root.setAttribute('data-theme-mode', mode);
  root.style.colorScheme = effective;
  if (animate && typeof window !== 'undefined') {
    root.classList.remove('theme-switching');
    window.requestAnimationFrame(() => {
      root.classList.add('theme-switching');
      if (themeSwitchTimer) window.clearTimeout(themeSwitchTimer);
      themeSwitchTimer = window.setTimeout(() => root.classList.remove('theme-switching'), 460);
    });
  }
}

export function useThemeController() {
  const [mode, setModeState] = useState<ThemeMode>(getStoredTheme);
  const [effectiveTheme, setEffectiveTheme] = useState<EffectiveTheme>(() => resolveTheme(getStoredTheme()));

  const syncMode = useCallback((next: ThemeMode, animate = false) => {
    setModeState(next);
    const effective = resolveTheme(next);
    setEffectiveTheme(effective);
    applyTheme(next, effective, animate);
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    window.localStorage.setItem(STORAGE_KEY, next);
    syncMode(next, true);
    window.dispatchEvent(new CustomEvent<ThemeMode>(CHANGE_EVENT, { detail: next }));
  }, [syncMode]);

  useEffect(() => {
    syncMode(mode);

    if (mode !== 'system' || typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
      const next = resolveTheme('system');
      setEffectiveTheme(next);
      applyTheme('system', next, true);
    };
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [mode, syncMode]);

  useEffect(() => {
    const onExternalChange = (event: Event) => {
      const detail = (event as CustomEvent<ThemeMode>).detail;
      syncMode(detail === 'light' || detail === 'dark' || detail === 'system' ? detail : getStoredTheme(), true);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) syncMode(getStoredTheme(), true);
    };
    window.addEventListener(CHANGE_EVENT, onExternalChange);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onExternalChange);
      window.removeEventListener('storage', onStorage);
    };
  }, [syncMode]);

  const cycleMode = useCallback(() => {
    const index = ORDER.indexOf(mode);
    setMode(ORDER[(index + 1) % ORDER.length]);
  }, [mode, setMode]);

  return { mode, effectiveTheme, setMode, cycleMode };
}

export function ThemeToggleButton() {
  const { mode, effectiveTheme, cycleMode } = useThemeController();
  const icon = useMemo(() => {
    return effectiveTheme === 'dark' ? <MoonOutlined /> : <SunOutlined />;
  }, [effectiveTheme]);
  return (
    <Button
      type="text"
      className="theme-toggle-button"
      data-mode={mode}
      data-effective-theme={effectiveTheme}
      icon={<span key={`${mode}-${effectiveTheme}`} className="theme-toggle-icon">{icon}</span>}
      aria-label="切换主题"
      onClick={cycleMode}
    />
  );
}
