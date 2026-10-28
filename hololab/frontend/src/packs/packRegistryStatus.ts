import type { NodeStatusPresentation } from '../canvas/nodeStatus';
import type { CatalogPack } from '../wire';

type CentralComparison = CatalogPack['central'];

/**
 * Presents a package's distribution source before its registry comparison.
 * Built-in packages deliberately have no registry counterpart: they ship with
 * HoloLab itself, so `central.state === "absent"` is expected and healthy.
 */
export function packRegistryStatus(name: string, comparison: CentralComparison, localCodeHash?: string | null): NodeStatusPresentation {
  if (name.startsWith('builtin/')) {
    return {
      tone: 'success',
      conclusion: '内置包',
      reason: '随 HoloLab 本体分发，无需在社区注册表中发布。',
      action: '这是受本体版本管理的可信包；升级 HoloLab 可取得更新。',
    };
  }
  if (comparison?.state === 'identical') return {
    tone: 'success', conclusion: '社区版',
    reason: `社区注册表：${comparison.source}\n代码哈希：${comparison.central_code_sha256}`,
    action: '本地内容与线上同名版本一致。',
  };
  if (comparison?.state === 'differs') return {
    tone: 'warning', conclusion: '与线上不同',
    reason: `社区注册表：${comparison.source}\n线上哈希：${comparison.central_code_sha256}\n本地哈希：${localCodeHash ?? '不可用'}`,
    action: '本地修改可正常开发；安装或发布前请确认要保留哪一版。',
  };
  if (comparison?.state === 'absent') return {
    tone: 'neutral', conclusion: '本地包', reason: '社区注册表没有同名同版本。', action: '这是本地定义的包，可照常开发和运行。',
  };
  return {
    tone: 'neutral', conclusion: '未查询', reason: comparison?.reason ?? '尚未取得注册表结果。', action: '本地包仍可使用；可稍后刷新重试。',
  };
}
