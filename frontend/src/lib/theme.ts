import { Row } from './api';
// Brand colours are stored per business and applied as CSS variables; globals.css derives the rest with color-mix().
export const defaultTheme = { primary: '#276f5b', accent: '#c9e8bd', sidebar: 'light' };
export const presets = [
  { name: 'Meşə', primary: '#276f5b', accent: '#c9e8bd' }, { name: 'Okean', primary: '#1d5fa8', accent: '#bfdcf7' },
  { name: 'Bənövşə', primary: '#6d3fc0', accent: '#ddd0fa' }, { name: 'Gül', primary: '#b93a6b', accent: '#f8cfe0' },
  { name: 'Narıncı', primary: '#c25b14', accent: '#fbd9b5' }, { name: 'Qəhvə', primary: '#7a4b2a', accent: '#ecd8c4' },
  { name: 'Qrafit', primary: '#2f3a45', accent: '#d3dae1' }, { name: 'Firuzə', primary: '#0f7c80', accent: '#bfeaea' },
];
export function themeOf(business?: Row | null) { return { ...defaultTheme, ...(business?.theme || {}) }; }
export function applyTheme(theme: Row, root: HTMLElement = document.documentElement) {
  root.style.setProperty('--brand', theme.primary || defaultTheme.primary);
  root.style.setProperty('--brand-accent', theme.accent || defaultTheme.accent);
  root.dataset.sidebar = theme.sidebar || 'light';
}
