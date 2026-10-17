import { afterEach, expect, it, vi } from 'vitest';
import { copyLibraryReference, libraryReference } from './libraryReference';
import type { Resource } from './api';
const file: Resource = {resource_id:'12345678-1234-1234-1234-123456789abc', folder_id:'root', name:'demo # 中文.mp4', resource_type:'video-source', size_bytes:12345, status:'ready', ready_count:1, target_count:1, placements:[{node_id:'node-a',node_name:'kiri4090',online:1,desired_state:'present',observed_state:'ready',last_error:null}], transfers:[]};
const path = () => '/library/';
afterEach(() => vi.unstubAllGlobals());
function setup(insertReference?: ReturnType<typeof vi.fn>) {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('window', {location:{origin:'http://lan:8828'},isSecureContext:true,flops:insertReference ? {version:1,insertReference} : undefined});
  vi.stubGlobal('navigator', {clipboard:{writeText}});
  return writeText;
}
it('copies a resolvable token, exact path, placement and instance links in a browser', async () => {
  const write = setup();
  expect((await copyLibraryReference(file,path)).state).toBe('copied');
  const text = write.mock.calls[0][0];
  expect(text).toContain(`hololab://library-resource/${file.resource_id}\n`);
  expect(text).toContain('"/library/demo # 中文.mp4"');
  expect(text).toContain('12345 bytes');
  expect(text).toContain('kiri4090 (node-a; ready; online)');
  expect(text).toContain('http://lan:8828/api/resolve?ref=');
});
it('inserts one structured resource reference without copying', async () => {
  const insert = vi.fn().mockResolvedValue({success:true}); const write = setup(insert);
  expect((await copyLibraryReference(file,path)).state).toBe('inserted');
  expect(insert).toHaveBeenCalledTimes(1);
  expect(insert.mock.calls[0][0]).toMatchObject({schemaVersion:1, provider:'hololab',type:'library_resource',resource:{uri:`http://lan:8828/api/library/resources/${file.resource_id}`}});
  expect(write).not.toHaveBeenCalled();
});
it.each([vi.fn().mockResolvedValue({success:false,reason:'unsupported-kind'}),vi.fn().mockRejectedValue(new Error('timeout'))])('copies on rejected or uncertain insertion, without retrying', async insert => {
  const write = setup(insert);
  const result = await copyLibraryReference(file,path);
  expect(result.state).toBe('copied'); expect(result.insertionError).toBeTruthy();
  expect(insert).toHaveBeenCalledTimes(1); expect(write).toHaveBeenCalledTimes(1);
});
it('reports clipboard failure with retry text', async () => {
  setup().mockRejectedValue(new Error('denied'));
  const result = await copyLibraryReference(file,path);
  expect(result.state).toBe('err'); expect(result.retryText).toContain(file.resource_id);
});
it('references folders by their own identity and navigable folder URL', () => {
  setup(); const ref = libraryReference({folder_id:file.resource_id,parent_id:'root',name:'Demo'}, () => '/library/Demo');
  expect(ref.payload.type).toBe('library_folder');
  expect(ref.payload.resource.uri).toBe(`http://lan:8828/library?folder=${file.resource_id}`);
  expect(ref.text).toContain(`hololab://library-folder/${file.resource_id}`);
  expect(ref.text).not.toContain('bytes');
});
