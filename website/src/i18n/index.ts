import labels from './metadata.json';
import en from './en.json';
import zh from './zh.json';

export type Locale = 'en' | 'zh-CN';
export const locale: Locale = /^\/zh-CN(?:\/|$)/.test(location.pathname) ? 'zh-CN' : 'en';
export const localeKey = 'hololab.website.locale';
export function localize(path: string, language: Locale = locale): string {
  const base = path.replace(/^\/zh-CN(?=\/|$)/, '') || '/';
  return language === 'zh-CN' ? `/zh-CN${base}` : base;
}
export const t = (key: keyof typeof en): string => (locale === 'en' ? en : zh)[key];

// Navigation metadata belongs to the UI; repository Markdown remains untouched.
export const label = (text: string): string => locale === 'en' ? (labels as Record<string, string>)[text] ?? text : text;
