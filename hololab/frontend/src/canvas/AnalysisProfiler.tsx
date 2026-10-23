import { Profiler, type ReactNode } from 'react';
import { analysisMetrics, analysisProfile, analysisDebug } from './analysisMetrics';

/** Opt-in real canvas Profiler, independent of node runtime and status. */
export function AnalysisProfiler({children}:{children:ReactNode}) {
  if (!analysisDebug) return <>{children}</>;
  return <Profiler id="workflow-canvas" onRender={(id,phase,actualDuration,baseDuration,startTime,commitTime)=>{
    analysisProfile.push({id,phase,actualDuration,baseDuration,startTime,commitTime,replaced:analysisMetrics.replaced});
    if (analysisProfile.length>1000) analysisProfile.shift();
  }}>{children}</Profiler>;
}
