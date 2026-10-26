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
