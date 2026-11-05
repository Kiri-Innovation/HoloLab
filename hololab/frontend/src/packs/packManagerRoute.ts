import {packageKey} from '../packageIdentity';

export const localSections = ['overview','category','attention','missing','undeclared'] as const;
export const communitySections = ['home','recent','category','publisher','scope','installed','uninstalled'] as const;
export function packManagerRoute(search:string) {
  const params=new URLSearchParams(search);
  const view=params.get('view')==='community'?'community':'local';
  const section=params.get('section') ?? '';
  const requestedPack=params.get('pack') ?? '';
  const separator=requestedPack.lastIndexOf('@');
  // Local entries use packageKey; legacy unscoped deep links must use it too.
  // Keep community URLs and incomplete references unchanged.
  const pack=view==='local' && separator>0 && separator<requestedPack.length-1
    ? packageKey(requestedPack.slice(0,separator),requestedPack.slice(separator+1))
    : requestedPack;
  return {view, localSection:localSections.find(value=>value===section) ?? 'overview',
    communitySection:communitySections.find(value=>value===section) ?? 'home',
    pack, category:params.get('category') ?? '', publisher:params.get('publisher') ?? '', scope:params.get('scope') ?? '',
    tree:params.get('tree') === 'scope' ? 'scope' : 'category'} as const;
}
export function packManagerPath(view:'local'|'community',section:string,extra:Record<string,string>={}) {
  const params=new URLSearchParams({view,section});
  for(const [key,value] of Object.entries(extra)) if(value)params.set(key,value);
  return `/packs?${params}`;
}
