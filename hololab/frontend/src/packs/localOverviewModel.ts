import type {EnvironmentReport} from '../canvas/EnvironmentChecks';
import {installedOn,type PackEntry} from './packManagerModel';
export function isUndeclared(entry:PackEntry) {return !!entry.catalog && entry.catalog.requires == null;}
export function localOverview(entries:PackEntry[],nodeId:string,reports:Record<string,EnvironmentReport>,problem:(entry:PackEntry)=>boolean) {
  const missing=entries.filter(entry=>reports[entry.key]?.status==='missing');
  const attention=entries.filter(problem).sort((a,b)=>Number(reports[b.key]?.status==='missing')-Number(reports[a.key]?.status==='missing') || a.key.localeCompare(b.key));
  const lastEdit=(entry:PackEntry)=>Math.max(0,...entry.usages.map(usage=>usage.workflow.updated_ts ?? 0));
  const recent=entries.filter(entry=>entry.usages.length>0).sort((a,b)=>lastEdit(b)-lastEdit(a) || b.usages.length-a.usages.length || a.key.localeCompare(b.key));
  return {installed:entries.filter(entry=>installedOn(entry,nodeId)).length,undeclared:entries.filter(isUndeclared),missing,attention,recent};
}

export function localPublishedTime(entry: PackEntry): number {
  const value = entry.catalog?.publication?.published_at;
  const timestamp = typeof value === 'string' ? Date.parse(value) : NaN;
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}
export function recentlyUpdatedPacks(entries: PackEntry[]): PackEntry[] {
  return [...entries].sort((a, b) => {
    const at = localPublishedTime(a), bt = localPublishedTime(b);
    return at === bt ? a.key.localeCompare(b.key) : at > bt ? -1 : 1;
  });
}
