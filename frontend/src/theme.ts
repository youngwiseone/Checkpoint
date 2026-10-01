import { useEffect } from "react";
import { useSettings } from "./state";

export type Theme = "dark" | "light";

const THEME_KEY = "checkpoint.theme";

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* cache only; settings are the source of truth */ }
}

/** Apply the last known theme before first render so there's no flash. */
export function applyCachedTheme() {
  let theme: Theme = "dark";
  try { if (localStorage.getItem(THEME_KEY) === "light") theme = "light"; } catch { /* default to dark */ }
  document.documentElement.dataset.theme = theme;
}

/** Keeps <html data-theme> in step with settings.appearance.theme. */
export function useThemeSync() {
  const { data } = useSettings();
  const theme: Theme | undefined = data ? (data.appearance?.theme === "light" ? "light" : "dark") : undefined;
  useEffect(() => { if (theme) applyTheme(theme); }, [theme]);
}
