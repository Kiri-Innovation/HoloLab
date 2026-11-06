import { loadColmap4dPayload } from './colmap4dPayload';
self.onmessage = async (event: MessageEvent<{baseUrl: string}>) => {
  try {
    const payload = await loadColmap4dPayload(event.data.baseUrl, text => self.postMessage({type: 'progress', text}));
    self.postMessage({type: 'loaded', payload});
  } catch (error) { self.postMessage({type: 'error', text: (error as Error).message}); }
};
