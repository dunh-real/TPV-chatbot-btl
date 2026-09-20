/* ===========================================================================
 * Lớp gọi API. Một chỗ duy nhất biết đường dẫn backend và hình dạng response,
 * để phần giao diện không phải nhớ endpoint nào nhận gì.
 * ======================================================================== */
(function (global) {
  'use strict';

  var LS_KEY = 'tpv.apiBase';
  var LS_TENANT = 'tpv.tenantId';

  function defaultBase() {
    // Mở qua chính backend (uvicorn mount /ui) -> gọi cùng origin.
    // Mở bằng file:// -> trỏ về cổng 8080 mặc định.
    if (location.protocol === 'http:' || location.protocol === 'https:') return location.origin;
    return 'http://localhost:8080';
  }

  var base = localStorage.getItem(LS_KEY) || defaultBase();

  function setBase(value) {
    base = (value || '').replace(/\/+$/, '') || defaultBase();
    localStorage.setItem(LS_KEY, base);
    return base;
  }
  function url(path) { return base + path; }

  /* Thuê bao đang xem. Backend đọc `X-Tenant-Id` (app/core/context.py) và chỉ
     rơi về `ERP_TENANT_ID` trong .env khi request không khai gì - bỏ trống ô này
     là giữ đúng hành vi cũ. Gửi chuỗi rỗng thì backend coi là khai sai định dạng
     và chặn, nên header chỉ được gắn khi thật sự có giá trị. */
  var tenant = localStorage.getItem(LS_TENANT) || '';

  function getTenant() { return tenant; }
  function setTenant(value) {
    tenant = String(value == null ? '' : value).trim();
    localStorage.setItem(LS_TENANT, tenant);
    return tenant;
  }

  /** Gắn định danh vào mọi lời gọi - một chỗ duy nhất, để không sót endpoint. */
  function withIdentity(headers) {
    var out = Object.assign({}, headers || {});
    if (tenant) out['X-Tenant-Id'] = tenant;
    return out;
  }

  function ApiError(message, status, detail) {
    this.name = 'ApiError';
    this.message = message;
    this.status = status;
    this.detail = detail;
  }
  ApiError.prototype = Object.create(Error.prototype);

  async function parseError(res) {
    var detail = '';
    try {
      var data = await res.json();
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
    } catch (e) {
      try { detail = await res.text(); } catch (e2) { detail = ''; }
    }
    if (detail && detail.length > 500) detail = detail.slice(0, 500) + '…';
    return new ApiError(detail || ('HTTP ' + res.status), res.status, detail);
  }

  async function request(path, options) {
    var res;
    options = Object.assign({}, options || {});
    options.headers = withIdentity(options.headers);
    try {
      res = await fetch(url(path), options);
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      throw new ApiError('Không kết nối được backend tại ' + base + ' — kiểm tra uvicorn đã chạy chưa.', 0, String(e));
    }
    if (!res.ok) throw await parseError(res);
    if (res.status === 204) return null;
    var ct = res.headers.get('content-type') || '';
    return ct.indexOf('application/json') >= 0 ? res.json() : res.text();
  }

  function postJSON(path, body, signal) {
    return request(path, {
      method: 'POST',
      headers: withIdentity({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body || {}),
      signal: signal,
    });
  }

  function postForm(path, formData, signal) {
    return request(path, { method: 'POST', body: formData, signal: signal });
  }

  /** SSE qua POST: tự tách khung `event:` / `data:` từ stream. */
  async function postSSE(path, body, handlers, signal) {
    var res;
    try {
      res = await fetch(url(path), {
        method: 'POST',
        headers: withIdentity({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
        body: JSON.stringify(body || {}),
        signal: signal,
      });
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      throw new ApiError('Không kết nối được backend tại ' + base + '.', 0, String(e));
    }
    if (!res.ok) throw await parseError(res);
    if (!res.body) throw new ApiError('Trình duyệt không đọc được stream', 0, '');

    var reader = res.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buffer = '';

    function dispatch(chunk) {
      var event = 'message';
      var dataLines = [];
      chunk.split('\n').forEach(function (line) {
        if (line.indexOf('event:') === 0) event = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).replace(/^ /, ''));
      });
      if (!dataLines.length) return;
      var payload;
      try { payload = JSON.parse(dataLines.join('\n')); } catch (e) { payload = { raw: dataLines.join('\n') }; }
      var fn = handlers[event];
      if (fn) fn(payload);
    }

    for (;;) {
      var step = await reader.read();
      if (step.done) break;
      buffer += decoder.decode(step.value, { stream: true });
      var parts = buffer.split('\n\n');
      buffer = parts.pop();
      parts.forEach(function (p) { if (p.trim()) dispatch(p); });
    }
    if (buffer.trim()) dispatch(buffer);
  }

  global.API = {
    ApiError: ApiError,
    getBase: function () { return base; },
    setBase: setBase,
    getTenant: getTenant,
    setTenant: setTenant,
    url: url,

    // hệ thống
    health: function () { return request('/health'); },
    tools: function () { return request('/api/agent/tools'); },

    // agent tổng
    agentChat: function (body, signal) { return postJSON('/api/agent/chat', body, signal); },
    agentChatStream: function (body, handlers, signal) {
      return postSSE('/api/agent/chat/stream', body, handlers, signal);
    },
    agentUpload: function (file, kind) {
      var fd = new FormData();
      fd.append('file', file);
      fd.append('kind', kind || 'upload');
      return postForm('/api/agent/upload', fd);
    },
    agentDownload: function (fileName) { return url('/api/agent/download/' + encodeURIComponent(fileName)); },

    // workflow 1
    qa: function (body, signal) { return postJSON('/api/chat/qa', body, signal); },
    qaStream: function (body, handlers, signal) { return postSSE('/api/chat/qa/stream', body, handlers, signal); },
    search: function (body, signal) { return postJSON('/api/chat/search', body, signal); },
    history: function (id) { return request('/api/chat/history/' + encodeURIComponent(id)); },
    clearHistory: function (id) { return request('/api/chat/history/' + encodeURIComponent(id), { method: 'DELETE' }); },
    newConversation: function () { return postJSON('/api/chat/conversations', {}); },

    // tài liệu / workflow 2
    corpusUpload: function (file, docType) {
      var fd = new FormData();
      fd.append('file', file);
      fd.append('doc_type', docType || '');
      return postForm('/api/documents/upload', fd);
    },
    ingestText: function (body) { return postJSON('/api/documents/ingest-text', body); },
    ruleSets: function () { return request('/api/documents/rule-sets'); },
    review: function (file, opts, signal) {
      opts = opts || {};
      var fd = new FormData();
      fd.append('file', file);
      fd.append('noi_gui', opts.noiGui || '');
      fd.append('rule_set', opts.ruleSet || '');
      return postForm('/api/documents/review', fd, signal);
    },
    stats: function () { return request('/api/documents/stats'); },
    deleteDoc: function (docId) { return request('/api/documents/' + encodeURIComponent(docId), { method: 'DELETE' }); },

    // workflow 3 / 4
    templates: function () { return request('/api/reports/templates'); },
    draft: function (body, signal) { return postJSON('/api/reports/draft', body, signal); },
    aggregate: function (body, signal) { return postJSON('/api/reports/aggregate', body, signal); },
    reportDownload: function (fileName) { return url('/api/reports/download/' + encodeURIComponent(fileName)); },

    // workflow 5
    presentation: function (body, signal) { return postJSON('/api/presentations/create', body, signal); },
    slideDownload: function (fileName) { return url('/api/presentations/download/' + encodeURIComponent(fileName)); },
  };
})(window);
