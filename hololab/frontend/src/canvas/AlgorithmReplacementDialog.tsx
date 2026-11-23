import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CatalogPack } from '../wire';
import { replacementChoices } from './algorithmReplacement';
import './algorithmReplacement.css';

export function AlgorithmReplacementDialog({current, catalog, candidates, unavailableReason, basis, notice, onSelect, onClose}: {
  current: CatalogPack; catalog: CatalogPack[]; candidates: CatalogPack[]; unavailableReason?: string; basis?: string; notice?: string; onSelect: (pack: CatalogPack) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const shown = replacementChoices(current, catalog, candidates)
    .filter(({pack}) => `${pack.name}@${pack.version}`.toLowerCase().includes(query.toLowerCase()));
  return createPortal(<dialog ref={dialog} className="hl-algorithm-replacement" aria-labelledby="replacement-title"
    onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => {if (event.target === dialog.current) onClose();}}>
    <header><h2 id="replacement-title">替换为算法</h2><button type="button" aria-label="关闭" onClick={onClose}>×</button></header>
    {basis && <p>{basis}</p>}
    {notice && <p>{notice}</p>}
    {unavailableReason && <p>{unavailableReason}</p>}
    <input autoFocus aria-label="搜索算法" placeholder="搜索算法名或版本" value={query} onChange={event => setQuery(event.target.value)} />
    <div className="hl-algorithm-replacement-list">
      {shown.map(({pack, active, canReplace}) => (
        <button key={`${pack.name}@${pack.version}`} type="button" disabled={!canReplace} onClick={() => onSelect(pack)}>
          <span className="hl-algorithm-replacement-candidate">
            <span className="hl-algorithm-replacement-identity" title={`${pack.name}@${pack.version}`}>
              <span className="hl-algorithm-replacement-name">{pack.name}</span> <small>@{pack.version}</small>
            </span>
            {active ? <small>当前</small> : !canReplace && <small>{unavailableReason ? '接口未验证' : '接口有变，需重新接线'}</small>}
          </span>
        </button>
      ))}
      {!shown.length && (query ? <p>未找到匹配的算法</p> : !unavailableReason && <p>无相同输入输出的算法</p>)}
    </div>
  </dialog>, document.body);
}
