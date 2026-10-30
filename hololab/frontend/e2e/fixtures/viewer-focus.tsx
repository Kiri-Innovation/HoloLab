import {createRoot} from 'react-dom/client';
import {ReactFlow, ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/theme/tokens.css';
import {NodeViewerFrame} from '../../src/canvas/NodeViewerFrame';
import {Preview} from '../../src/canvas/previews';
import {LagsplatPreview} from '../../src/canvas/LagsplatPreview';
const scrollTest = new URLSearchParams(location.search).has('scroll');
function FrameNode({id}: {id: string}) {
  return <div className="nodrag" style={{width:300,padding:10,background:'var(--surface-2)'}}><div>{id}</div>{id==='text' ? <Preview baseUrl="/fixture-text" storage="file" spec={{viewer:'text',member:null}} /> : id==='colmap' ? <Preview baseUrl="/fixture-colmap" storage="dir" tags={['colmap-points']} /> : id==='lagsplat'
    ? <LagsplatPreview baseUrl="/model" />
    : <NodeViewerFrame viewer={{id:scrollTest?'training_diagnostics':'custom',title:id,renderer:'iframe',entry:'viewer.html',default:true}}
      packName="test" packVersion="1" assignedNodeId="device" frames={new URLSearchParams(location.search).has('images') ? [1,2].map(iteration=>({iteration,proxy_url:`/fixture-ssim/${iteration}.png`,node_id:'device'})) : []} running={scrollTest} progress={scrollTest?{current:50,total:100}:null}
      workflowViewLoaded workflowViewManaged={false} sceneUrl={id==='square'?'/fixture-square':undefined} />}</div>;
}
const nodes=(scrollTest?['custom','text']:['custom','lagsplat','square','colmap']).map((id,i)=>({id,type:'frame',position:{x:50+i*360,y:80},data:{}}));
createRoot(document.getElementById('root')!).render(<ReactFlowProvider><div style={{height:'100vh'}}>
  <ReactFlow nodes={nodes} nodeTypes={{frame:FrameNode}} minZoom={.2} maxZoom={3}/>
</div></ReactFlowProvider>);
