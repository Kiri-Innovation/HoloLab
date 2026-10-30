import {createRoot} from 'react-dom/client';
import {ComputeNodesPanel} from '../../src/canvas/ComputeNodesPanel';
import '../../src/styles.css';
const node = {node_id:'test',node_name:'Test compute node',packs:[],gpu:{count:0}};
createRoot(document.getElementById('root')!).render(<ComputeNodesPanel nodes={[node as any]} metricsByNode={{}} />);
