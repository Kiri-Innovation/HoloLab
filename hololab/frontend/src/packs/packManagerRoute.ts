export const localSections = ['overview','category','attention','missing','undeclared'] as const;
export const communitySections = ['home','recent','category','publisher','scope','installed','uninstalled'] as const;
export function packManagerRoute(search:string) {
  const params=new URLSearchParams(search);
  const view=params.get('view')==='community'?'community':'local';
  const section=params.get('section') ?? '';
  return {view, localSection:localSections.find(value=>value===section) ?? 'overview',
    communitySection:communitySections.find(value=>value===section) ?? 'home',
    pack:params.get('pack') ?? '', category:params.get('category') ?? '', publisher:params.get('publisher') ?? '', scope:params.get('scope') ?? ''} as const;
}
export function packManagerPath(view:'local'|'community',section:string,extra:Record<string,string>={}) {
  const params=new URLSearchParams({view,section});
  for(const [key,value] of Object.entries(extra)) if(value)params.set(key,value);
  return `/packs?${params}`;
}
