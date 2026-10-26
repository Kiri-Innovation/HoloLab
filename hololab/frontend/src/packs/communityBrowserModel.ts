import {releaseCategory} from './releaseCategory';

export type CommunityRelease = {name:string; version:string; yanked:boolean; code_sha256:string; published_at:string; publisher:string; requires:unknown; env:string; category?:unknown; evidence?:{manifest?:{normalized?:{category?:unknown}}}};
export type CommunityProject = {name:string; releases?:CommunityRelease[]; error?:string};
export type CommunityFilter = {search:string; category:string; publisher:string; scope:string; installed:'all'|'installed'|'uninstalled'};
export function publishedTime(value: string): number {
  const time=Date.parse(value);
  return Number.isFinite(time) ? time : Number.NEGATIVE_INFINITY;
}
export function latestRelease(project:CommunityProject) {
  return project.releases?.reduce<CommunityRelease|undefined>((latest,release)=>!latest || publishedTime(release.published_at)>publishedTime(latest.published_at) ? release : latest,undefined);
}
export function projectCategory(project:CommunityProject):string[] {
  const category=releaseCategory(latestRelease(project));
  return category.length ? category : ['misc'];
}
export function browseProjects(projects:CommunityProject[], filter:CommunityFilter, isInstalled:(r:CommunityRelease)=>boolean):CommunityProject[] {
  const query=filter.search.trim().toLowerCase();
  return projects.filter(project=>{
    const latest=latestRelease(project), category=projectCategory(project).join('/');
    const installed=project.releases?.some(isInstalled) ?? false;
    return `${project.name} ${latest?.publisher ?? ''} ${category}`.toLowerCase().includes(query)
      && (!filter.category || category===filter.category || category.startsWith(`${filter.category}/`))
      && (!filter.publisher || latest?.publisher===filter.publisher)
      && (!filter.scope || project.name.split('/')[0]===filter.scope)
      && (filter.installed==='all' || (!!project.releases && (filter.installed==='installed' ? installed : !installed)));
  }).sort((a,b)=>{
    const at=publishedTime(latestRelease(a)?.published_at ?? ''), bt=publishedTime(latestRelease(b)?.published_at ?? '');
    return at===bt ? a.name.localeCompare(b.name) : at>bt ? -1 : 1;
  });
}
