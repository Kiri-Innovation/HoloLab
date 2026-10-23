/** Opt-in browser instrumentation; tests can reset/read the same counters. */
export const analysisMetrics = { ws:0, sent:0, completed:0, cancelled:0, failed:0, replaced:0 };
export const analysisProfile: {id:string; phase:string; actualDuration:number; baseDuration:number; startTime:number; commitTime:number; replaced:number}[] = [];
export const analysisDebug = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('debug') === '1';
export function resetAnalysisMetrics() { for (const key of Object.keys(analysisMetrics) as (keyof typeof analysisMetrics)[]) analysisMetrics[key]=0; analysisProfile.length=0; }
if (analysisDebug) {
  Object.assign(window, {hololabAnalysis:{counts:analysisMetrics,profile:analysisProfile,reset:resetAnalysisMetrics}});
}
