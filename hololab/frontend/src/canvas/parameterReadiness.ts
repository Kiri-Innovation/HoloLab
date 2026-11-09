import type {EnvironmentReport} from './EnvironmentChecks';

export type PathParameterSpecs = Record<string, {type: string; optional?: boolean; default?: unknown}>;

/** Display evidence only. Never changes report status or execution validation. */
export function parameterReadiness(report?: EnvironmentReport, specs?: PathParameterSpecs) {
  const issues = report?.issues.filter(issue => issue.item === 'prepare_params') ?? [];
  if (!issues.length) return undefined;
  const missing = new Set<string>();
  const details = issues.map(issue => String(issue.actual ?? '')).join('\n');
  for (const issue of issues) {
    const values = issue.required && typeof issue.required === 'object' && !Array.isArray(issue.required)
      ? issue.required as Record<string, unknown> : {};
    // `required` is a path-values snapshot, including optional paths, not a required-field list.
    if (specs) for (const [name, spec] of Object.entries(specs)) {
      if (spec.type !== 'path' || spec.optional || !(name in values)) continue;
      const value = values[name];
      if (value == null || (typeof value === 'string' && !value.trim())) missing.add(name);
    }
    if (!specs) {
      // Without schema, only name the field proven invalid by the node's error.
      const match = String(issue.actual ?? '').match(/Parameter ([\w.-]+): enter a nonempty path string/);
      if (match) missing.add(match[1]);
    }
  }
  const hasEnvironmentIssues = report!.issues.some(issue => issue.item !== 'prepare_params');
  return {
    conclusion: missing.size ? '必填参数未填' : '本地参数准备失败',
    reason: [missing.size ? `缺少必填路径参数：${[...missing].join('、')}` : '', details,
      hasEnvironmentIssues ? '另有环境检查问题，请查看环境证据。' : '声明依赖探测与参数准备是两项检查；依赖满足不代表参数已填写。'].filter(Boolean).join('\n'),
    action: missing.size ? '填写上述计算节点路径后重新准备；当前配置不能启动新任务。'
      : '检查参数值、路径或执行节点连接后重新准备；当前配置不能启动新任务。',
  };
}

/** Combine analysis evidence for display; retain all real capability reports. */
export function preparationDisplayReports(reports: Record<string, EnvironmentReport>, analysis?: {
  issues?: {kind: string; node_id?: string; message: string}[];
  graph: {nodes: {id: string; params?: Record<string, unknown>}[]};
}) {
  const result = {...reports};
  for (const issue of analysis?.issues ?? []) {
    if (issue.kind !== 'prepare_params' || !issue.node_id) continue;
    const prior = result[issue.node_id];
    result[issue.node_id] = {...prior, status: prior?.status ?? 'unknown', issues: [
      ...(prior?.issues ?? []), {item: 'prepare_params',
        required: analysis?.graph.nodes.find(node => node.id === issue.node_id)?.params ?? {},
        actual: issue.message, remedy: '检查该节点的本地路径参数后重新准备'},
    ]};
  }
  return result;
}

export function preparationBlockerText(prepared: {run_blockers?: string[]; environment_checks?: Record<string, EnvironmentReport>}) {
  return (prepared.run_blockers ?? []).map(blocker => {
    const entry = Object.entries(prepared.environment_checks ?? {}).find(([id]) => blocker.startsWith(`${id} `) || blocker.startsWith(`${id}:`));
    if (!entry || !blocker.includes('prepare_params')) return blocker;
    const [id, report] = entry;
    const problem = parameterReadiness(report);
    if (!problem) return blocker;
    const other = report.issues.filter(issue => issue.item !== 'prepare_params')
      .map(issue => `${issue.item}: ${String(issue.actual)}; ${issue.remedy}`).join('\n');
    return [`${id} (${report.source ?? ''}): ${problem.conclusion}`, problem.reason, problem.action, other].filter(Boolean).join('\n');
  }).join('\n');
}


/** Older gateways mix unrelated graph issues into a single-node preflight.
 * Remove only blockers proven to belong to other nodes. Keep target/global
 * issues and unstructured failures, including identical target messages.
 */
export function scopeNodePreparation<T extends {
  run_blockers?: string[];
  issues?: {node_id?: string; message: string}[];
}>(prepared: T, nodeId?: string): T {
  if (!nodeId || !prepared.issues?.length || !prepared.run_blockers?.length) return prepared;
  const other = new Map<string, number>();
  const relevant = new Map<string, number>();
  const total = new Map<string, number>();
  for (const issue of prepared.issues) {
    const counts = issue.node_id && issue.node_id !== nodeId ? other : relevant;
    counts.set(issue.message, (counts.get(issue.message) ?? 0) + 1);
  }
  for (const blocker of prepared.run_blockers) total.set(blocker, (total.get(blocker) ?? 0) + 1);
  const remove = new Map([...other].map(([message, count]) => [message,
    Math.min(count, Math.max(0, (total.get(message) ?? 0) - (relevant.get(message) ?? 0)))]));
  return {...prepared, run_blockers: prepared.run_blockers.filter(blocker => {
    const remaining = remove.get(blocker) ?? 0;
    if (!remaining) return true;
    remove.set(blocker, remaining - 1);
    return false;
  })};
}
