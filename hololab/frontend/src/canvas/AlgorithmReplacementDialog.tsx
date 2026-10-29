import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CatalogPack } from '../wire';
import { samePackage } from '../packageIdentity';
import './algorithmReplacement.css';

export function AlgorithmReplacementDialog({current, candidates, onSelect, onClose}: {
  current: CatalogPack; candidates: CatalogPack[]; onSelect: (pack: CatalogPack) => void; onClose: () => void;
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
      {shown.map(pack => {const active = samePackage(current.name, current.version, pack.name, pack.version);
        return <button key={`${pack.name}@${pack.version}`} type="button" disabled={active} onClick={() => onSelect(pack)}>
          <span>{pack.name} <small>@{pack.version}</small></span>{active && <small>当前</small>}
        </button>;
      })}
      {!shown.length && <p>无相同输入输出的算法</p>}
    </div>
  </dialog>, document.body);
}
