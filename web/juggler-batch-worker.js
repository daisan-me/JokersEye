'use strict';
importScripts('/juggler-math.js', '/juggler-batch-core.js');
let processor;
self.addEventListener('message', event => {
  try {
    if (event.data.type === 'init') {
      processor = JokersJugglerBatch.createProcessor(event.data.catalog, event.data.config);
      self.postMessage({type: 'ready'});
    } else if (event.data.type === 'batch') {
      if (!processor) throw Error('計算エンジンが準備できていません。');
      self.postMessage({type: 'results', rows: event.data.rows.map(processor)});
    }
  } catch (error) { self.postMessage({type: 'error', message: error.message}); }
});
