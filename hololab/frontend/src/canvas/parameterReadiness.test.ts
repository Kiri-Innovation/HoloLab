import {expect,it} from 'vitest';
import {preparationDisplayReports,parameterReadiness} from './parameterReadiness';
import {nodeStatus} from './nodeStatus';
import {environmentLabel, type EnvironmentReport} from './EnvironmentChecks';
const specs={megasam_repo:{type:'path'},sharp_repo:{type:'path'},tracker_python:{type:'path'},exposure_metadata_path:{type:'path',optional:true}};
const report:EnvironmentReport={status:'unknown',issues:[{item:'prepare_params',required:{megasam_repo:null,sharp_repo:null,tracker_python:null,exposure_metadata_path:null},actual:'Parameter megasam_repo: enter a nonempty path string',remedy:'检查路径'}]};
it('lists mandatory null paths without misclassifying optional metadata',()=>{
 const result=parameterReadiness(report,specs)!;
 expect(result.conclusion).toBe('必填参数未填');
 for(const name of ['megasam_repo','sharp_repo','tracker_python'])expect(result.reason).toContain(name);
 expect(result.reason).not.toContain('exposure_metadata_path');
 expect(report.status).toBe('unknown');
 expect(nodeStatus({environment:report,parameterSpecs:specs}).conclusion).toBe('必填参数未填');
 expect(environmentLabel(report)).toBe('必填参数未填');
});
it('does not infer that every null snapshot value is required when schema is unavailable',()=>{
 expect(parameterReadiness(report)!.reason).toContain('megasam_repo');
 expect(parameterReadiness(report)!.reason).not.toContain('exposure_metadata_path');
});
it('distinguishes invalid paths, disconnected preparation, and actual environment failures',()=>{
 for(const actual of ['Parameter megasam_repo: use an absolute path on the compute node','connection timeout']){
  const invalid:EnvironmentReport={status:'unknown',issues:[{...report.issues[0],required:{megasam_repo:'/repo'},actual}]};
  expect(parameterReadiness(invalid,specs)!.conclusion).toBe('本地参数准备失败');
 }
 const env:EnvironmentReport={status:'missing',issues:[{item:'ffmpeg',required:'installed',actual:'missing',remedy:'install'}]};
 expect(parameterReadiness(env,specs)).toBeUndefined();
 expect(nodeStatus({environment:env}).conclusion).toBe('本地能力检查失败');
 expect(parameterReadiness({...report,issues:[...report.issues,...env.issues]},specs)!.reason).toContain('另有环境检查问题');
});
it('does not replace live execution or snapshot status with current preparation errors',()=>{
 expect(nodeStatus({state:'running',environment:report,parameterSpecs:specs}).conclusion).toBe('正在运行');
 expect(nodeStatus({state:'done',readOnly:true,environment:report,parameterSpecs:specs}).conclusion).toBe('此快照中的运行已完成');
});

it('uses analyze issues without dropping capability reports or mutating their statuses',()=>{
 const env:EnvironmentReport={status:'satisfied',issues:[]};
 const merged=preparationDisplayReports({a:env,b:env},{graph:{nodes:[{id:'a',params:report.issues[0].required as Record<string,unknown>}]},issues:[{kind:'prepare_params',node_id:'a',message:String(report.issues[0].actual)}]});
 expect(merged.b).toBe(env);
 expect(merged.a.status).toBe('satisfied');
 expect(env.issues).toHaveLength(0);
 expect(parameterReadiness(merged.a,specs)!.conclusion).toBe('必填参数未填');
});
