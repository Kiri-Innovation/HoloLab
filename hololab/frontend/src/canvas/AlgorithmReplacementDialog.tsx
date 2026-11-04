import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CatalogPack } from '../wire';
import { samePackage } from '../packageIdentity';
import type { ReplacementParameterChange } from './algorithmReplacement';
import './algorithmReplacement.css';

export function AlgorithmReplacementDialog({current, candidates, unavailableReason, basis, parameterChanges, onSelect, onClose}: {
  current: CatalogPack; candidates: CatalogPack[]; unavailableReason?: string; basis?: string; parameterChanges?: (pack: CatalogPack) => ReplacementParameterChange[]; onSelect: (pack: CatalogPack) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const shown = candidates.filter(pack => `${pack.name}@${pack.version}`.toLowerCase().includes(query.toLowerCase()));
  return createPortal(<dialog ref={dialog} className="hl-algorithm-replacement" aria-labelledby="replacement-title"
    onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => {if (event.target === dialog.current) onClose();}}>
    <header><h2 id="replacement-title">替换为算法</h2><button type="button" aria-label="关闭" onClick={onClose}>×</button></header>
    <input autoFocus aria-label="搜索算法" placeholder="搜索算法名或版本" value={query} onChange={event => setQuery(event.target.value)} />
    <div className="hl-algorithm-replacement-list">
      {basis && <p>{basis}</p>}
      {shown.map(pack => {const active = samePackage(current.name, current.version, pack.name, pack.version); const changes = parameterChanges?.(pack) ?? [];
        // Keep semantic migrations (for example skip's changed meaning) visible
        // before incidental defaults from parameters introduced by a release.
        const orderedChanges = [...changes].sort((a, b) => Number(a.reason === '目标版本默认值') - Number(b.reason === '目标版本默认值'));
        const visibleChanges = orderedChanges.slice(0, 3);
        return <button key={`${pack.name}@${pack.version}`} type="button" disabled={active} onClick={() => onSelect(pack)}>
          <span className="hl-algorithm-replacement-candidate"><span className="hl-algorithm-replacement-name" title={`${pack.name}@${pack.version}`}>{pack.name} <small>@{pack.version}</small></span>{active && <small>当前</small>}</span>
          {!active && visibleChanges.length > 0 && <span className="hl-algorithm-replacement-changes">
            {visibleChanges.map(change => <small key={change.name}>{change.name}: {String(change.from)} → {String(change.to)}（{change.reason}）</small>)}
            {orderedChanges.length > visibleChanges.length && <small>还有 {orderedChanges.length - visibleChanges.length} 项参数将使用目标版本设置</small>}
          </span>}
        </button>;
      })}
      {!shown.length && <p>{unavailableReason ?? "无相同输入输出的算法"}</p>}
    </div>
  </dialog>, document.body);
}
