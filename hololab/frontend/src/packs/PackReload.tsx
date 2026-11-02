import {useState} from 'react';
import {CONTROL_STYLE} from '../ui/controlStyles';

export function PackReload({onReload}: {onReload: () => void}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function reload() {
    setBusy(true);
    setMessage('正在重扫所有在线节点…');
    try {
      const response = await fetch('/api/packs/reload', {method: 'POST'});
      if (!response.ok) throw new Error(`刷新失败 (${response.status})`);
      const data: {nodes: {node_id: string; count?: number; manifest_changed?: boolean; error?: string}[]} = await response.json();
      onReload();
      setMessage(data.nodes.length ? data.nodes.map(node => node.error
        ? `${node.node_id}: ${node.error}`
        : `${node.node_id.slice(0, 8)}：${node.count} 个包 · ${node.manifest_changed ? '声明有变化' : '声明无变化，已重载源文件'}`).join('；') : '没有在线节点');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '刷新失败');
    } finally {setBusy(false);}
  }
  return <div style={{padding: '8px 12px', borderBottom: '1px solid var(--border-subtle)'}}>
    <button type="button" disabled={busy} onClick={() => void reload()} style={CONTROL_STYLE}>
      {busy ? '刷新中…' : '重新扫描包'}
    </button>
    {message && <div role="status" style={{marginTop: 6, color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', overflowWrap: 'anywhere'}}>{message}</div>}
  </div>;
}
