import type {ReactNode} from 'react';
import type {PackEntry} from './packManagerModel';
import {localPublishedTime, recentlyUpdatedPacks} from './localOverviewModel';

export const localStatusPages = {
  attention: {title: '需要注意', description: '列出需要处理的包，具体原因见各项说明；未声明环境要求本身不属于问题。'},
  missing: {title: '环境缺失', description: '列出当前机器缺少已声明环境能力的包，按具体缺失项处理。'},
  undeclared: {title: '未声明', description: '这些包尚未声明环境要求；未声明不等于环境缺失，也不保证一定能运行。'},
} as const;

type ListProps = {
  items: PackEntry[];
  displayName: (name: string) => string;
  statusLabel: (entry: PackEntry) => string;
  onSelect: (key: string) => void;
};
function PackResults({items, displayName, statusLabel, onSelect, recent = false}: ListProps & {recent?: boolean}) {
  return <div className="hl-local-results">{items.map(entry => {
    const timestamp = localPublishedTime(entry);
    return <article className="hl-local-result" key={entry.key}>
      <div><button className="hl-pack-project-link" onClick={() => onSelect(entry.key)}>{displayName(entry.name)}</button>
        <span className="hl-pack-version">{entry.version}</span>
        <p>{statusLabel(entry)}</p>
        <small>{entry.catalog?.category?.join(' / ') || 'misc'} · {new Set(entry.usages.map(u => u.workflow.workflow_id)).size} 条工作流引用</small>
      </div>
      {recent && <span className="hl-local-published">{Number.isFinite(timestamp)
        ? <time dateTime={new Date(timestamp).toISOString()}>{new Date(timestamp).toLocaleDateString()}</time>
        : '发布时间未记录'}</span>}
    </article>;
  })}</div>;
}

export function LocalOverviewPage({search, onSearch, feedback, loading, ...list}: ListProps & {
  search: string; onSearch: (value: string) => void; feedback: ReactNode; loading: boolean;
}) {
  const query = search.trim().toLowerCase();
  const items = recentlyUpdatedPacks(list.items).filter(entry =>
    `${entry.name}@${entry.version} ${entry.catalog?.category?.join('/') || 'misc'}`.toLowerCase().includes(query));
  return <div className="hl-local-overview-page" data-local-page="overview">
    <h1>总览</h1>
    <label className="hl-community-search">搜索本地包<input type="search" aria-label="搜索包" placeholder="搜索包名、版本或功能分类…" value={search} onChange={e => onSearch(e.target.value)} /></label>
    {feedback}
    <section aria-label={query ? '搜索结果' : '近期更新'}>
      <h2>{query ? '搜索结果' : '近期更新'}</h2>
      {!query && <p className="hl-pack-boundary">按已记录的发布时间排序；未记录时间的包列在末尾。</p>}
      {!loading && <PackResults {...list} items={items} recent />}
      {!loading && !items.length && <p className="hl-pack-empty">{list.items.length ? '没有符合搜索条件的包。' : '还没有发现包。连接计算节点或前往社区安装包。'}</p>}
    </section>
  </div>;
}

export function LocalResultsPage({title, description, kind, feedback, loading, emptyMessage, ...list}: ListProps & {
  title: string; description?: string; kind: string; feedback: ReactNode; loading: boolean; emptyMessage: string;
}) {
  return <div className="hl-local-results-page" data-local-page={kind}>
    <header><h1>{title}</h1>{description && <p className="hl-pack-boundary">{description}</p>}</header>
    {feedback}
    {!loading && <><p className="hl-local-result-count">{list.items.length} 个版本</p><PackResults {...list} />
      {!list.items.length && <p className="hl-pack-empty">{emptyMessage}</p>}</>}
  </div>;
}
