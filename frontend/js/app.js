/* ===========================================================================
 * TPV Trợ lý — lớp giao diện.
 *
 * Mỗi "view" là một màn hình độc lập, dùng chung vài hàm dựng khối hiển thị
 * (nguồn trích dẫn, file sinh ra, bảng, JSON thô). Mọi lời gọi backend đi qua
 * window.API, mọi chuỗi do model sinh đi qua window.MD (đã escape).
 * ======================================================================== */
(function () {
  'use strict';

  /* ───────────────────────────── tiện ích ───────────────────────────── */
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var esc = function (s) { return MD.escape(s); };

  function el(tag, attrs, html) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'dataset') Object.assign(node.dataset, attrs[k]);
      else if (k.indexOf('on') === 0) node.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null) node.setAttribute(k, attrs[k]);
    });
    if (html != null) node.innerHTML = html;
    return node;
  }

  function fmtBytes(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function fmtNum(n) {
    if (n == null || n === '') return '—';
    if (typeof n !== 'number') return esc(String(n));
    return n.toLocaleString('vi-VN');
  }

  function prettyJSON(value) {
    var text = esc(JSON.stringify(value, null, 2) || '');
    return text
      .replace(/(&quot;[^&]*?&quot;)(\s*:)/g, '<span class="k">$1</span>$2')
      .replace(/:\s(&quot;.*?&quot;)/g, ': <span class="s">$1</span>')
      .replace(/:\s(-?\d+\.?\d*)/g, ': <span class="n">$1</span>')
      .replace(/:\s(true|false|null)/g, ': <span class="b">$1</span>');
  }

  var toastBox = $('#toasts');
  function toast(message, kind, ms) {
    var node = el('div', { class: 'toast ' + (kind || '') }, esc(message));
    toastBox.appendChild(node);
    setTimeout(function () {
      node.classList.add('out');
      setTimeout(function () { node.remove(); }, 250);
    }, ms || (kind === 'err' ? 6500 : 3200));
  }

  function errText(e) {
    if (!e) return 'Lỗi không rõ';
    if (e.name === 'AbortError') return 'Đã huỷ';
    return e.message || String(e);
  }

  function uid() { return Math.random().toString(36).slice(2, 10); }

  /* ────────────────────────── trạng thái lưu ────────────────────────── */
  var PREFS_KEY = 'tpv.prefs';
  var CONVOS_KEY = 'tpv.convos';

  var prefs = Object.assign({
    theme: 'light',
    unit: '',
    inputs: { nguoi_ky: '', chuc_vu_ky: '' },
    topN: '',
    docIds: '',
    docTypes: '',
    sources: '',
    trace: false,
  }, JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'));

  function savePrefs() { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); }

  var convos = JSON.parse(localStorage.getItem(CONVOS_KEY) || '[]');
  function saveConvos() { localStorage.setItem(CONVOS_KEY, JSON.stringify(convos.slice(0, 40))); }

  /* ───────────────────────────── giao diện ──────────────────────────── */
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    prefs.theme = theme;
    savePrefs();
  }
  applyTheme(prefs.theme);
  $('#themeBtn').addEventListener('click', function () {
    applyTheme(prefs.theme === 'dark' ? 'light' : 'dark');
  });

  var app = $('#app');
  function setCollapsed(on) {
    app.classList.toggle('collapsed', on);
    $('#collapseBtn').setAttribute('aria-expanded', String(!on));
    try { localStorage.setItem('tpv.collapsed', on ? '1' : '0'); } catch (e) { /* chế độ riêng tư */ }
  }

  $('#collapseBtn').addEventListener('click', function () {
    setCollapsed(!app.classList.contains('collapsed'));
  });
  $('#expandBtn').addEventListener('click', function () { setCollapsed(false); });
  $('#menuBtn').addEventListener('click', function () { app.classList.toggle('nav-open'); });

  /* Khôi phục lựa chọn lần trước. Người thu gọn thanh bên thường muốn nó ở
     nguyên vậy; mở lại tab mà nó bung ra là phải bấm lại mỗi lần. */
  try {
    if (localStorage.getItem('tpv.collapsed') === '1') setCollapsed(true);
  } catch (e) { /* chế độ riêng tư: bỏ qua, mặc định là bung */ }
  $('#scrim').addEventListener('click', function () { app.classList.remove('nav-open'); });

  var VIEWS = {
    chat: ['Agent tổng', 'Một câu yêu cầu — hệ thống tự chọn workflow, kể cả hỏi đáp tài liệu'],
    review: ['Soát tài liệu', 'Workflow 2 · rule engine cấu trúc + soát chữ nghĩa + phân rã nhiệm vụ'],
    draft: ['Soạn báo cáo', 'Workflow 3 · đọc một tài liệu tải lên, kiểm chứng từng con số trước khi xuất file'],
    aggregate: ['Tổng hợp báo cáo', 'Workflow 4 · nhiều đơn vị, biểu đồ do code vẽ, đối chiếu file đã gửi'],
    slides: ['Tạo slide', 'Workflow 5 · hệ thống soạn nội dung từng slide, Presenton render'],
    mindmap: ['Sơ đồ tư duy', 'Đọc cả tài liệu bằng MAP-REDUCE rồi dựng cây chủ đề; nội dung từng mục sinh khi bấm vào'],
    ocr: ['OCR tài liệu', 'Mô hình thị giác đọc chữ trong ảnh trang, trả về Markdown giữ nguyên cấu trúc'],
    corpus: ['Kho tri thức', 'Nạp tài liệu vào Qdrant và xem thống kê collection'],
    retrieval: ['Truy hồi (debug)', 'Xem hạng từng nhánh, điểm RRF và điểm rerank'],
    tools: ['Công cụ & hệ thống', 'Danh mục tool của agent và trạng thái hạ tầng'],
  };

  var currentView = 'chat';
  function showView(name) {
    if (!VIEWS[name]) name = 'chat';
    currentView = name;
    $$('.view').forEach(function (v) { v.classList.toggle('is-active', v.id === 'view-' + name); });
    $$('.nav-item').forEach(function (b) { b.classList.toggle('is-active', b.dataset.view === name); });
    $('#viewTitle').textContent = VIEWS[name][0];
    $('#viewSub').textContent = VIEWS[name][1];
    app.classList.remove('nav-open');
    location.hash = name;
    if (name === 'tools') loadToolsPage();
    if (name === 'corpus') loadStats();
    if (name === 'review' && !ruleSetsLoaded) loadRuleSets();
    if (name === 'ocr') ocrLoadStatus();
  }
  $$('.nav-item').forEach(function (b) {
    b.addEventListener('click', function () { showView(b.dataset.view); });
  });

  /* ─────────────────────────── health polling ───────────────────────── */
  var lastHealth = null;
  async function pollHealth() {
    var dot = $('#healthDot'), text = $('#healthText');
    dot.className = 'dot pulse';
    try {
      var h = await API.health();
      lastHealth = h;
      var allOk = h.qdrant && h.llm && h.database;
      dot.className = 'dot ' + (allOk ? 'ok' : (h.qdrant || h.llm ? 'warn' : 'err'));
      var down = [];
      if (!h.qdrant) down.push('Qdrant');
      if (!h.llm) down.push('LLM');
      if (!h.database) down.push('CSDL');
      text.textContent = allOk ? ('Sẵn sàng · ' + fmtNum(h.points) + ' điểm') : ('Thiếu: ' + down.join(', '));
    } catch (e) {
      lastHealth = null;
      dot.className = 'dot err';
      text.textContent = 'Không kết nối backend';
    }
    if (currentView === 'tools') loadToolsPage();
  }
  $('#healthBtn').addEventListener('click', function () { showView('tools'); pollHealth(); });
  pollHealth();
  setInterval(pollHealth, 30000);

  /* ───────────────────────── khối hiển thị chung ────────────────────── */
  function block(title, count, bodyHTML, open) {
    var wrap = el('div', { class: 'block' + (open ? ' open' : '') });
    var head = el('button', { class: 'block-head', type: 'button' },
      esc(title) + (count != null ? ' <span class="block-count">' + count + '</span>' : '') +
      '<svg class="caretic" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>');
    head.addEventListener('click', function () { wrap.classList.toggle('open'); });
    var body = el('div', { class: 'block-body' });
    if (typeof bodyHTML === 'string') body.innerHTML = bodyHTML; else if (bodyHTML) body.appendChild(bodyHTML);
    wrap.appendChild(head);
    wrap.appendChild(body);
    return wrap;
  }

  function sourceHTML(item, idx) {
    var title = item.doc_title || item.label || item.doc_id || ('Nguồn ' + idx);
    var meta = [];
    if (item.kind) meta.push(esc(item.kind));
    if (item.section) meta.push(esc(item.section));
    if (item.source) meta.push(esc(item.source));
    if (item.page != null) meta.push('trang ' + item.page);
    if (item.doc_id && item.doc_title) meta.push(esc(item.doc_id));
    if (item.score != null) meta.push('score ' + Number(item.score).toFixed(3));
    if (item.locator) {
      Object.keys(item.locator).slice(0, 3).forEach(function (k) {
        meta.push(esc(k) + ': ' + esc(String(item.locator[k])));
      });
    }
    return '<div class="source" data-src="' + (item.id != null ? item.id : idx) + '">' +
      '<span class="source-idx">' + (item.id != null ? item.id : idx) + '</span>' +
      '<div class="source-body">' +
      '<div class="source-title">' + esc(title) + '</div>' +
      (meta.length ? '<div class="source-meta">' + meta.map(function (m) { return '<span>' + m + '</span>'; }).join('') + '</div>' : '') +
      (item.snippet ? '<div class="source-snip">' + esc(item.snippet) + '</div>' : '') +
      '</div></div>';
  }

  function sourcesBlock(title, items) {
    if (!items || !items.length) return null;
    var html = items.map(function (it, i) { return sourceHTML(it, i + 1); }).join('');
    var node = block(title, items.length, html, false);
    node.addEventListener('click', function (ev) {
      var src = ev.target.closest('.source');
      if (src) src.classList.toggle('expanded');
    });
    return node;
  }

  function artifactsNode(artifacts, resolver) {
    if (!artifacts || !artifacts.length) return null;
    var wrap = el('div', { class: 'artifacts' });
    artifacts.forEach(function (a) {
      var name = a.file_name || a.name || '';
      var kind = (a.kind || name.split('.').pop() || '').toLowerCase();
      var href = a.download_url ? API.getBase() + a.download_url : (resolver ? resolver(name) : '#');
      wrap.appendChild(el('a', { class: 'artifact', href: href, target: '_blank', rel: 'noopener', download: name },
        '<span class="artifact-ic ' + (['docx', 'pptx', 'pdf'].indexOf(kind) >= 0 ? kind : 'other') + '">' + esc(kind.toUpperCase() || 'FILE') + '</span>' +
        '<span class="artifact-body"><span class="artifact-name">' + esc(name) + '</span>' +
        '<span class="artifact-sub">Bấm để tải về</span></span>'));
    });
    return wrap;
  }

  function tableHTML(table) {
    if (!table || !table.columns) return '';
    return '<div class="table-scroll"><table><thead><tr>' +
      table.columns.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') +
      '</tr></thead><tbody>' +
      (table.rows || []).map(function (r) {
        return '<tr>' + r.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
  }

  function validationPill(v) {
    if (!v || !v.status) return '';
    var map = { passed: 'ok', warning: 'warn', failed: 'err', skipped: '' };
    var label = { passed: 'Số liệu khớp', warning: 'Có cảnh báo', failed: 'Không đạt', skipped: 'Bỏ qua kiểm chứng' };
    return '<span class="pill ' + (map[v.status] || '') + '">' + esc(label[v.status] || v.status) +
      (v.checked_numbers ? ' · ' + v.checked_numbers + ' số' : '') + '</span>';
  }

  function validationNode(v) {
    if (!v || !v.status) return null;
    var body = '<div class="tag-row" style="margin-bottom:8px">' + validationPill(v) + '</div>';
    if (v.issues && v.issues.length) {
      body += v.issues.map(function (it) {
        return '<div class="finding"><span class="finding-badge pill ' + (it.severity === 'error' ? 'err' : 'warn') + '">' + esc(it.severity || '') + '</span>' +
          '<div class="finding-body"><div class="finding-msg">' + esc(it.type) + ' · mục ' + esc(it.section) + '</div>' +
          (it.numbers && it.numbers.length ? '<div class="finding-meta"><span>Số chưa truy được: ' + esc(it.numbers.join(', ')) + '</span></div>' : '') +
          (it.quote ? '<div class="quote">' + esc(it.quote) + '</div>' : '') + '</div></div>';
      }).join('');
    } else {
      body += '<div class="muted sm">Không có vấn đề nào.</div>';
    }
    return block('Kiểm chứng số liệu', (v.issues || []).length, body, (v.issues || []).length > 0);
  }

  function listBlock(title, items, cls) {
    if (!items || !items.length) return null;
    return block(title, items.length,
      '<div class="tag-row">' + items.map(function (i) {
        return '<span class="pill ' + (cls || '') + '">' + esc(typeof i === 'string' ? i : JSON.stringify(i)) + '</span>';
      }).join('') + '</div>', false);
  }

  /* Ô chờ của các workflow dài. `hint` = {text, slowAfter} bật thêm đồng hồ đếm
     giây - cùng lý do với đồng hồ trong `liveTimeline`: mấy endpoint này trả về
     MỘT lần, không phát sự kiện tiến trình, nên con số nhích đều là bằng chứng
     rẻ nhất rằng hệ thống còn sống. Chỉ đếm thời gian THẬT; không vẽ thanh tiến
     trình giả theo mốc đoán trước, vì quá mốc là nó đứng im và nói dối.
     Node trả về có `.stop()` - phải gọi, nếu không `setInterval` sống tiếp sau
     khi ô chờ bị gỡ khỏi DOM. */
  function loaderNode(label, onCancel, hint) {
    var node = el('div', { class: 'loader' }, '<span class="spinner"></span>');
    var text = el('div', { class: 'loader-text' },
      '<span class="loader-label"></span><span class="loader-note"></span>');
    $('.loader-label', text).textContent = label || 'Đang xử lý…';
    node.appendChild(text);
    node.stop = function () {};

    if (hint) {
      var note = $('.loader-note', text);
      var clock = el('span', { class: 'loader-time' }, '0s');
      node.appendChild(clock);
      note.textContent = hint.text || '';
      var t0 = Date.now();
      var timer = setInterval(function () {
        var giay = Math.round((Date.now() - t0) / 1000);
        clock.textContent = giay + 's';
        /* Quá lâu thì nói thẳng là lâu, đừng để nguyên câu "thường 60-90 giây"
           trong khi đồng hồ đã 140s - người xem sẽ tin là đã treo. */
        if (hint.slowAfter && giay > hint.slowAfter) {
          node.classList.add('is-slow');
          note.textContent = 'lâu hơn thường lệ — vẫn đang chạy, bấm Huỷ nếu muốn dừng';
        }
      }, 1000);
      node.stop = function () { clearInterval(timer); };
    }

    if (onCancel) {
      var btn = el('button', { class: 'ghost-btn sm' }, 'Huỷ');
      btn.addEventListener('click', onCancel);
      node.appendChild(btn);
    }
    return node;
  }

  /** Chạy một workflow dài: khoá nút, hiện loader có nút huỷ, dựng kết quả. */
  async function runWorkflow(opts) {
    var btn = opts.button, box = opts.box;
    var ctrl = new AbortController();
    btn.disabled = true;
    box.innerHTML = '';
    var loader = loaderNode(opts.label, function () { ctrl.abort(); }, opts.hint);
    box.appendChild(loader);
    try {
      var data = await opts.call(ctrl.signal);
      loader.stop();
      box.innerHTML = '';
      opts.render(data);
    } catch (e) {
      loader.stop();
      box.innerHTML = '';
      if (e && e.name === 'AbortError') {
        box.appendChild(el('div', { class: 'result-empty' }, 'Đã huỷ yêu cầu.'));
      } else {
        box.appendChild(el('div', { class: 'card' }, '<div style="color:var(--err)">' + esc(errText(e)) + '</div>'));
        toast(errText(e), 'err');
      }
    } finally { btn.disabled = false; }
  }

  /* ───────────────────── popover cho marker trích dẫn ───────────────── */
  var sourceRegistry = new Map();
  var popover = $('#popover');

  function hidePopover() { popover.hidden = true; }
  document.addEventListener('click', function (ev) {
    var cite = ev.target.closest('.cite');
    if (!cite) { if (!ev.target.closest('.popover')) hidePopover(); return; }
    var host = cite.closest('[data-mid]');
    var list = host ? sourceRegistry.get(host.dataset.mid) : null;
    var n = Number(cite.dataset.cite);
    var item = (list || []).filter(function (s) { return Number(s.id) === n; })[0] || (list || [])[n - 1];
    if (!item) { toast('Không tìm thấy nguồn [' + n + ']'); return; }

    var meta = [item.kind, item.section, item.source, item.doc_id, item.page != null ? 'trang ' + item.page : ''].filter(Boolean);
    popover.innerHTML = '<h4>[' + n + '] ' + esc(item.doc_title || item.label || 'Nguồn') + '</h4>' +
      (meta.length ? '<div class="source-meta">' + meta.map(function (m) { return '<span>' + esc(m) + '</span>'; }).join('') + '</div>' : '') +
      '<div class="pop-text">' + esc(item.snippet || '(không có trích đoạn)') + '</div>';
    popover.hidden = false;
    var r = cite.getBoundingClientRect();
    var top = r.bottom + 8, left = Math.min(Math.max(12, r.left - 80), window.innerWidth - popover.offsetWidth - 12);
    if (top + popover.offsetHeight > window.innerHeight - 12) top = Math.max(12, r.top - popover.offsetHeight - 8);
    popover.style.top = top + 'px';
    popover.style.left = left + 'px';
  });
  window.addEventListener('resize', hidePopover);

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: AGENT CHAT
     ══════════════════════════════════════════════════════════════════ */
  var chat = {
    conversationId: null,
    attachments: [],   // {file_id, name, size}
    busy: false,
    controller: null,
  };

  var chatInput = $('#chatInput');
  var chatMessages = $('#chatMessages');
  var chatEmpty = $('#chatEmpty');
  var chatThread = $('#chatThread');

  function autoGrow(node) {
    node.style.height = 'auto';
    node.style.height = Math.min(node.scrollHeight, 220) + 'px';
  }
  [chatInput].forEach(function (n) {
    n.addEventListener('input', function () { autoGrow(n); });
  });

  function scrollThread(thread) {
    requestAnimationFrame(function () { thread.scrollTop = thread.scrollHeight; });
  }

  var TOOL_LABEL = {
    search_documents: 'kho tài liệu', get_document: 'nguyên văn văn bản',
    analyze_document: 'soát tài liệu', get_template: 'mẫu báo cáo',
    get_personnel_statistics: 'nhân sự', get_equipment_statistics: 'trang thiết bị',
    get_reporting_status: 'tình hình nộp báo cáo',
    generate_docx: 'dựng file .docx', generate_presentation: 'dựng file .pptx',
  };

  /* Phải khớp `INTENT_VI` trong backend/app/agents/graph.py: backend gửi nhãn
     kèm sự kiện tiến trình, còn bảng này dựng huy hiệu lúc chạy xong. Lệch nhau
     thì một lượt chạy đổi tên nghiệp vụ giữa chừng - đã từng là "Tra số liệu"
     lúc chạy rồi thành "Tool loop" lúc xong. */
  var INTENT_LABEL = {
    qa: 'Tra cứu tài liệu', document: 'Soát tài liệu', draft: 'Soạn văn bản',
    report: 'Tổng hợp báo cáo', presentation: 'Tạo slide', agent: 'Tra số liệu',
    clarify: 'Hỏi lại',
  };

  function intentPill(routing, intent) {
    var name = INTENT_LABEL[intent] || intent || '—';
    var conf = routing && routing.confidence ? ' · ' + Math.round(routing.confidence * 100) + '%' : '';
    var src = routing && routing.source ? ' · ' + routing.source : '';
    return '<span class="pill accent" title="' + esc((routing && routing.reason) || '') + '">' + esc(name + conf + src) + '</span>';
  }

  /* Gom các bước thành từng đợt chạy song song - cùng luật với `Plan.waves`
     bên backend: một bước vào được đợt hiện tại khi mọi thứ nó phụ thuộc đã
     xong ở đợt trước. */
  function planWaves(steps) {
    var remaining = steps.slice(), done = {}, waves = [], guard = 0;
    while (remaining.length && guard++ < 10) {
      var wave = remaining.filter(function (s) {
        return (s.depends_on || []).every(function (d) { return done[d]; });
      });
      if (!wave.length) wave = remaining.slice(0, 1);
      waves.push(wave);
      wave.forEach(function (s) { done[s.id] = true; });
      remaining = remaining.filter(function (s) { return wave.indexOf(s) < 0; });
    }
    return waves;
  }

  function planNode(plan, steps) {
    if (!plan || !plan.steps || !plan.steps.length) return null;
    var ran = {};
    (steps || []).forEach(function (s) { ran[s.id] = s; });

    var waves = planWaves(plan.steps);
    var retried = (steps || []).some(function (s) { return s.retried_as; });
    var wrap = el('div', { class: 'plan' });

    waves.forEach(function (wave, i) {
      var row = el('div', { class: 'plan-wave' });
      row.appendChild(el('span', { class: 'plan-tag' },
        esc('Đợt ' + (i + 1)) + (wave.length > 1 ? ' <b>· song song</b>' : '')));
      var list = el('div', { class: 'plan-steps' });
      wave.forEach(function (step) {
        var done = ran[step.id] || {};
        var cls = 'plan-step' + (done.empty ? ' is-empty' : '') + (done.error ? ' is-err' : '');
        var head = INTENT_LABEL[done.intent || step.intent] || step.intent;
        var note = '';
        if (done.retried_as) {
          note = 'không ra kết quả bằng ' + (INTENT_LABEL[done.planned_intent] || done.planned_intent) +
            ' → đã thử lại bằng ' + (INTENT_LABEL[done.retried_as] || done.retried_as);
        } else if (done.empty) {
          note = 'chạy xong nhưng không có dữ liệu';
        } else if (step.depends_on && step.depends_on.length) {
          note = 'cần kết quả của ' + step.depends_on.join(', ');
        }
        list.appendChild(el('div', { class: cls },
          '<div class="plan-step-head">' + esc(head) +
          (done.attempts > 1 ? ' <span class="pill warn">' + done.attempts + ' lần</span>' : '') + '</div>' +
          '<div class="plan-step-req">' + esc(step.request || '') + '</div>' +
          (note ? '<div class="plan-step-note">' + esc(note) + '</div>' : '')));
      });
      row.appendChild(list);
      wrap.appendChild(row);
    });

    /* Một bước chạy trơn tru thì kế hoạch không có gì để xem - gấp lại. Nhiều
       bước hoặc có bước phải thử lại thì mở sẵn: đó là lúc người dùng cần biết
       agent đã làm những gì. */
    var open = plan.steps.length > 1 || retried;
    return block('Kế hoạch thực hiện', plan.steps.length, wrap, open);
  }

  function userMessage(container, text, files) {
    var node = el('div', { class: 'msg msg-user' });
    if (files && files.length) {
      node.appendChild(el('div', { class: 'msg-files' }, files.map(function (f) {
        return '<span class="attach">📎 ' + esc(f.name) + '</span>';
      }).join('')));
    }
    node.appendChild(el('div', { class: 'bubble' }, esc(text)));
    container.appendChild(node);
    return node;
  }

  function botShell(container, name) {
    var mid = uid();
    var node = el('div', { class: 'msg msg-bot', dataset: { mid: mid } });
    node.innerHTML =
      '<div class="msg-head"><span class="avatar">TP</span><span class="msg-name">' + esc(name || 'Trợ lý') + '</span>' +
      '<span class="msg-badges"></span>' +
      '<span class="msg-actions"></span></div>' +
      '<div class="msg-body"><div class="prose"><div class="typing"><i></i><i></i><i></i></div></div>' +
      '<div class="msg-extra"></div></div>';
    container.appendChild(node);
    return {
      mid: mid,
      root: node,
      badges: $('.msg-badges', node),
      actions: $('.msg-actions', node),
      prose: $('.prose', node),
      extra: $('.msg-extra', node),
    };
  }

  function addCopyAction(shell, getText) {
    var btn = el('button', { class: 'icon-btn', title: 'Sao chép câu trả lời' },
      '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>');
    btn.addEventListener('click', function () {
      navigator.clipboard.writeText(getText()).then(function () { toast('Đã sao chép', 'ok', 1600); },
        function () { toast('Trình duyệt chặn clipboard', 'err'); });
    });
    shell.actions.appendChild(btn);
  }

  /* Dòng thời gian sống: thay vòng xoay bằng thứ agent đang thật sự làm.
     Chỉ hiện BƯỚC và suy luận - kết quả công cụ cố tình không có ở đây, vì số
     liệu thô chưa qua van đối chiếu thì chưa đáng để người đọc tin. */
  function liveTimeline(shell) {
    var wrap = el('div', { class: 'live' });
    var think = el('div', { class: 'live-think' }, '<i></i><span>Đang đọc yêu cầu…</span>');
    var list = el('div', { class: 'live-steps' });
    /* Chữ model sinh ra, hiện ngay khi nó về. Đây là văn bản thô đang chảy nên
       để `textContent` - markdown chỉ dựng lại một lần ở `done`, khi câu đã trọn
       vẹn và không còn bảng hay danh sách nào đứt giữa chừng. */
    var flow = el('div', { class: 'live-flow', hidden: 'hidden' });
    wrap.appendChild(think);
    wrap.appendChild(list);
    wrap.appendChild(flow);
    shell.prose.innerHTML = '';
    shell.prose.appendChild(wrap);
    var chu = '';

    var rows = {};
    var running = null;          // {id, t0, note} - dòng đang chạy
    var ticker = null;

    /* Đồng hồ đếm giây cho dòng đang chạy. Vòng lặp công cụ có lượt im lặng
       10-15 giây; con số nhích đều là bằng chứng rẻ nhất rằng hệ thống còn sống,
       và nó không cần backend phát thêm gì cả. */
    function tick() {
      if (!running) return;
      var giay = Math.round((Date.now() - running.t0) / 1000);
      var node = rows[running.id];
      if (node) $('.live-note', node).textContent = running.note + ' · ' + giay + 's';
    }

    function startTicker(id, note) {
      running = { id: id, t0: (running && running.id === id) ? running.t0 : Date.now(), note: note };
      tick();
      if (!ticker) ticker = setInterval(tick, 1000);
    }

    function stopTicker() {
      running = null;
      if (ticker) { clearInterval(ticker); ticker = null; }
    }

    function row(id) {
      if (!rows[id]) {
        var node = el('div', { class: 'live-step' },
          '<span class="live-dot"></span><span class="live-label"></span>' +
          '<span class="live-note"></span>');
        rows[id] = node;
        list.appendChild(node);
      }
      return rows[id];
    }

    return {
      /* Dựng sẵn khung đủ các bước rồi mới tô từng ô: người dùng biết ngay còn
         bao nhiêu việc nữa, thay vì thấy các dòng nhảy ra từng cái một. */
      plan: function (p) {
        (p.steps || []).forEach(function (st, i) {
          var node = row(st.id);
          node.className = 'live-step is-wait';
          $('.live-label', node).textContent = (i + 1) + '. ' + (INTENT_LABEL[st.intent] || st.intent);
          $('.live-note', node).textContent = (st.depends_on && st.depends_on.length)
            ? 'chờ ' + st.depends_on.join(', ') : '';
        });
        scrollThread(chatThread);
      },
      thinking: function (p) {
        if (!p.text) return;
        $('span', think).textContent = String(p.text).slice(0, 200);
      },
      stepStart: function (p) {
        var node = row(p.id);
        node.className = 'live-step is-run';
        $('.live-label', node).textContent = $('.live-label', node).textContent || p.label || p.intent;
        startTicker(p.id, 'đang chạy');
        scrollThread(chatThread);
      },
      /* Nhịp tim bên trong một bước dài. Tên công cụ là NHÃN VIỆC, không phải dữ
         liệu: không tham số, không kết quả - thứ đó thuộc về câu trả lời cuối. */
      stepProgress: function (p) {
        if (!running) return;
        var viec = (p.tools && p.tools.length)
          ? 'đang tra: ' + p.tools.map(function (t) { return TOOL_LABEL[t] || t; }).join(', ')
          : (p.phase || 'đang chạy');
        running.note = viec + (p.max_rounds ? ' (lượt ' + p.round + '/' + p.max_rounds + ')' : '');
        tick();
      },
      stepRetry: function (p) {
        var node = row(p.id);
        node.className = 'live-step is-retry';
        stopTicker();
        $('.live-note', node).textContent = 'không ra kết quả → thử lại bằng ' + (p.label || p.to);
        startTicker(p.id, 'thử lại bằng ' + (p.label || p.to));
      },
      stepDone: function (p) {
        var node = row(p.id);
        stopTicker();
        node.className = 'live-step ' + (p.error ? 'is-err' : (p.empty ? 'is-empty' : 'is-ok'));
        $('.live-note', node).textContent = p.error
          ? 'lỗi'
          : (p.empty ? 'không có dữ liệu'
            : (p.attempts > 1 ? 'xong bằng ' + (p.label || p.intent) : 'xong'));
        scrollThread(chatThread);
      },
      answerDelta: function (p) {
        if (!p.text) return;
        chu += p.text;
        flow.hidden = false;
        flow.textContent = chu;
        scrollThread(chatThread);
      },
      /* Chữ vừa hiện hoá ra là câu dẫn trước khi model gọi tool - dọn đi, nếu
         không người dùng đọc phải một câu bị bỏ dở rồi mâu thuẫn với câu cuối. */
      answerReset: function () {
        chu = '';
        flow.textContent = '';
        flow.hidden = true;
      },
      finish: function () { stopTicker(); think.remove(); },
      abort: function () { stopTicker(); },
    };
  }

  function renderAgentResult(shell, data) {
    var sources = (data.citations || []).concat(data.refs || []);
    sourceRegistry.set(shell.mid, sources);

    var buoc = (data.plan && data.plan.steps) || [];
    shell.badges.innerHTML = intentPill(data.routing, data.intent) +
      (buoc.length > 1 ? ' <span class="pill">' + buoc.length + ' bước</span>' : '') +
      (data.error ? ' <span class="pill err">lỗi</span>' : '') +
      (data.result && data.result.validation ? ' ' + validationPill(data.result.validation) : '');

    shell.prose.innerHTML = MD.render(data.answer || '(không có nội dung)');
    shell.extra.innerHTML = '';

    var plan = planNode(data.plan, data.steps);
    if (plan) shell.extra.appendChild(el('div', { style: 'margin-top:10px' })).appendChild(plan);

    if (data.error) {
      shell.extra.appendChild(el('div', { class: 'block open' },
        '<div class="block-body" style="color:var(--err)">' + esc(data.error) + '</div>'));
    }

    var art = artifactsNode(data.artifacts);
    if (art) { shell.extra.appendChild(el('div', { style: 'margin-top:12px' })).appendChild(art); }

    /* Tài liệu hội thoại đang nhớ. Hiện ra để người dùng biết "tài liệu đó" đang
       trỏ tới cái nào - nếu không, lượt sau họ gõ "tổng hợp tài liệu đó" mà
       không chắc agent hiểu là file nào trong mấy file đã gửi. */
    var sf = data.session_files || [];
    if (sf.length) {
      var box = el('div', { class: 'block', style: 'margin-top:10px' });
      box.innerHTML = '<div class="block-head">Tài liệu trong phiên <span class="block-count">'
        + sf.length + '</span></div>';
      var body = el('div', { class: 'block-body' });
      body.appendChild(el('div', { class: 'tag-row' })).innerHTML =
        sf.map(function (f, i) {
          return '<span class="pill' + (i === 0 ? ' accent' : '') + '" title="' + esc(f.file_id) + '">'
            + esc(f.ten) + (i === 0 ? ' · mới nhất' : '') + '</span>';
        }).join('');
      body.appendChild(el('p', { class: 'muted sm', style: 'margin-top:8px' },
        'Nói <b>"tài liệu đó"</b>, <b>"file vừa gửi"</b>… là agent dùng lại tài liệu mới nhất, '
        + 'không cần đính kèm lại. Hỏi số liệu bình thường thì vẫn đọc CSDL.'));
      box.appendChild(body);
      shell.extra.appendChild(box);
    }

    if (data.missing_input && data.missing_input.length) {
      var box = el('div', { class: 'block open', style: 'margin-top:10px' });
      box.innerHTML = '<div class="block-head" style="cursor:default">Thiếu thông tin <span class="block-count">' + data.missing_input.length + '</span></div>';
      var body = el('div', { class: 'block-body' });
      var chips = el('div', { class: 'missing' });
      data.missing_input.forEach(function (key) {
        var chip = el('button', { class: 'chip' }, esc(key) + ' →');
        chip.addEventListener('click', function () {
          if (key === 'ma_don_vi') { openDrawer(); $('#prefUnit').focus(); }
          else { addPrefInput(key, ''); openDrawer(); }
        });
        chips.appendChild(chip);
      });
      body.appendChild(chips);
      body.appendChild(el('p', { class: 'muted sm', style: 'margin-top:8px' },
        'Điền ở <b>Tham số</b> rồi gửi lại yêu cầu — agent dừng chứ không đoán bừa.'));
      box.appendChild(body);
      shell.extra.appendChild(box);
    }

    var citeBlock = sourcesBlock('Nguồn trích dẫn', data.citations);
    var refBlock = sourcesBlock('Nguồn của từng ý', data.refs);
    var wrapExtras = el('div', { style: 'margin-top:12px' });
    var hasExtra = false;
    [citeBlock, refBlock].forEach(function (b) { if (b) { wrapExtras.appendChild(b); hasExtra = true; } });

    if (data.result && Object.keys(data.result).length) {
      wrapExtras.appendChild(block('Kết quả thô của workflow', null, '<pre class="json">' + prettyJSON(data.result) + '</pre>', false));
      hasExtra = true;
    }
    if (data.trace && Object.keys(data.trace).length) {
      wrapExtras.appendChild(block('Trace pipeline', null, '<pre class="json">' + prettyJSON(data.trace) + '</pre>', false));
      hasExtra = true;
    }
    if (hasExtra) shell.extra.appendChild(wrapExtras);

    addCopyAction(shell, function () { return data.answer || ''; });
  }

  function rememberConvo(id, title, kind) {
    if (!id) return;
    var existing = convos.filter(function (c) { return c.id === id; })[0];
    if (existing) { existing.title = existing.title || title; existing.ts = Date.now(); }
    else convos.unshift({ id: id, title: title, kind: kind || 'agent', ts: Date.now() });
    saveConvos();
    renderConvos();
  }

  function renderConvos() {
    var list = $('#convoList');
    list.innerHTML = '';
    if (!convos.length) {
      list.appendChild(el('div', { class: 'convo-empty' }, 'Chưa có cuộc trò chuyện nào.'));
      return;
    }
    convos.slice(0, 40).forEach(function (c) {
      var active = c.id === chat.conversationId;
      var row = el('div', { class: 'convo' + (active ? ' is-active' : ''), title: c.id });
      var open = el('button', { class: 'convo-open' },
        '<span class="convo-title">' + esc(c.title || c.id) + '</span>' +
        '<span class="convo-kind">' + esc(c.kind) + '</span>');
      open.addEventListener('click', function () { loadConversation(c); });
      var del = el('button', { class: 'icon-btn convo-del', title: 'Xoá hội thoại này khỏi máy chủ' },
        '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13"/></svg>');
      del.addEventListener('click', async function (ev) {
        ev.stopPropagation();
        try { await API.clearHistory(c.id); } catch (e) { toast('Máy chủ báo lỗi khi xoá: ' + errText(e), 'err'); }
        convos = convos.filter(function (x) { return x.id !== c.id; });
        saveConvos();
        if (chat.conversationId === c.id) { chat.conversationId = null; chatMessages.innerHTML = ''; chatEmpty.hidden = false; }
        renderConvos();
        toast('Đã xoá hội thoại', 'ok', 2000);
      });
      row.appendChild(open);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  async function loadConversation(c) {
    // Hội thoại cũ kiểu `qa` (từ thời còn màn Hỏi đáp riêng) cũng mở ở đây:
    // agent tổng trả lời được câu hỏi tài liệu, nên không có gì để mất.
    showView('chat');
    var container = chatMessages;
    container.innerHTML = '';
    chatEmpty.hidden = true;
    chat.conversationId = c.id;
    renderConvos();

    var holder = el('div', { class: 'loader' }, '<span class="spinner"></span><span>Đang tải lịch sử…</span>');
    container.appendChild(holder);
    try {
      var data = await API.history(c.id);
      container.innerHTML = '';
      if (!data.turns || !data.turns.length) {
        container.appendChild(el('div', { class: 'muted sm', style: 'text-align:center' }, 'Hội thoại trống.'));
        return;
      }
      data.turns.forEach(function (t) {
        if (t.role === 'user') userMessage(container, t.content);
        else {
          var shell = botShell(container, 'Trợ lý');
          shell.prose.innerHTML = MD.render(t.content || '');
          addCopyAction(shell, function () { return t.content; });
        }
      });
      scrollThread(chatThread);
    } catch (e) {
      container.innerHTML = '';
      toast('Không tải được lịch sử: ' + errText(e), 'err');
    }
  }

  $('#clearConvosBtn').addEventListener('click', function () {
    convos = []; saveConvos(); renderConvos(); toast('Đã xoá danh sách hội thoại', 'ok');
  });

  $('#newChatBtn').addEventListener('click', async function () {
    chat.conversationId = null;
    chat.attachments = [];
    renderAttachments();
    chatMessages.innerHTML = '';
    chatEmpty.hidden = false;
    showView('chat');
    chatInput.focus();
    renderConvos();
    // Xin sẵn một mã hội thoại để lượt đầu tiên đã có chỗ ghi lịch sử; hỏng thì
    // bỏ qua, backend tự sinh mã khi nhận request.
    try {
      var res = await API.newConversation();
      if (!chat.conversationId) chat.conversationId = res.conversation_id;
    } catch (e) { /* không chặn việc gõ câu đầu tiên */ }
  });

  /* --- đính kèm file ---------------------------------------------------- */
  function renderAttachments() {
    var row = $('#chatAttachments');
    row.innerHTML = '';
    row.hidden = chat.attachments.length === 0;
    chat.attachments.forEach(function (a, idx) {
      var node = el('span', { class: 'attach' + (a.pending ? ' loading' : '') },
        '📎 ' + esc(a.name) + (a.size ? ' <span class="muted">' + fmtBytes(a.size) + '</span>' : ''));
      var x = el('button', { class: 'icon-btn', title: 'Bỏ file' },
        '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>');
      x.addEventListener('click', function () { chat.attachments.splice(idx, 1); renderAttachments(); });
      node.appendChild(x);
      row.appendChild(node);
    });
    $('#chatMeta').textContent = chat.attachments.length
      ? 'file_id: ' + chat.attachments.map(function (a) { return a.file_id || '…'; }).join(', ') : '';
  }

  $('#chatAttachBtn').addEventListener('click', function () { $('#chatFile').click(); });
  $('#chatFile').addEventListener('change', async function (ev) {
    var file = ev.target.files && ev.target.files[0];
    ev.target.value = '';
    if (!file) return;
    var entry = { name: file.name, size: file.size, pending: true };
    chat.attachments = [entry];   // backend nhận đúng một file_id mỗi lượt
    renderAttachments();
    try {
      var res = await API.agentUpload(file, 'upload');
      entry.file_id = res.file_id;
      entry.pending = false;
      renderAttachments();
      toast('Đã tải lên: ' + res.file_id, 'ok');
    } catch (e) {
      chat.attachments = [];
      renderAttachments();
      toast('Tải file thất bại: ' + errText(e), 'err');
    }
  });

  $$('#chatSuggest .chip').forEach(function (c) {
    c.addEventListener('click', function () {
      chatInput.value = c.dataset.prompt;
      autoGrow(chatInput);
      sendChat();
    });
  });

  function collectInputs(listId) {
    var out = {};
    $$('#' + listId + ' .kv-row').forEach(function (row) {
      var k = $('.kv-key', row).value.trim();
      var v = $('.kv-val', row).value.trim();
      if (k) out[k] = v;
    });
    return out;
  }

  var SEND_ICON = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
  var STOP_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2.5"/></svg>';

  /** Khi đang chạy, nút gửi đổi thành nút dừng - yêu cầu dài vài chục giây,
   *  không có đường thoát thì người dùng chỉ còn cách tải lại trang. */
  function setBusy(view, busy) {
    var btn = $('#chatSend');
    btn.disabled = false;
    btn.classList.toggle('stop', busy);
    btn.innerHTML = busy ? STOP_ICON : SEND_ICON;
    btn.title = busy ? 'Dừng' : 'Gửi (Enter)';
  }

  async function sendChat() {
    if (chat.busy) return;
    var text = chatInput.value.trim();
    if (!text) return;
    var pending = chat.attachments.filter(function (a) { return a.pending; });
    if (pending.length) { toast('File đang tải lên, đợi một chút…'); return; }

    chat.busy = true;
    setBusy('chat', true);
    chatEmpty.hidden = true;
    chatInput.value = '';
    autoGrow(chatInput);

    var files = chat.attachments.slice();
    userMessage(chatMessages, text, files);
    var shell = botShell(chatMessages, 'Trợ lý');
    scrollThread(chatThread);

    var body = {
      request: text,
      conversation_id: chat.conversationId,
      file_id: (files[0] && files[0].file_id) || '',
      ma_don_vi: prefs.unit || '',
      inputs: collectInputs('prefInputs'),
      include_trace: $('#traceToggle').checked,
    };

    chat.controller = new AbortController();
    var live = liveTimeline(shell);
    try {
      var data = null;
      var streamErr = null;
      await API.agentChatStream(body, {
        plan: live.plan,
        thinking: live.thinking,
        step_start: live.stepStart,
        step_progress: live.stepProgress,
        answer_delta: live.answerDelta,
        answer_reset: live.answerReset,
        step_retry: live.stepRetry,
        step_done: live.stepDone,
        done: function (p) { data = p; },
        error: function (p) { streamErr = p && p.detail; },
      }, chat.controller.signal);

      if (streamErr) throw new API.ApiError(streamErr, 500, '');
      /* Stream đứt giữa chừng mà không có `done`: hỏi lại bằng đường thường còn
         hơn bắt người dùng gõ lại câu vừa gửi. */
      if (!data) data = await API.agentChat(body, chat.controller.signal);

      live.finish();
      chat.conversationId = data.conversation_id;
      renderAgentResult(shell, data);
      rememberConvo(data.conversation_id, text.slice(0, 56), 'agent');
      chat.attachments = [];
      renderAttachments();
    } catch (e) {
      live.abort();
      var aborted = e && e.name === 'AbortError';
      shell.badges.innerHTML = '<span class="pill ' + (aborted ? '' : 'err') + '">' + (aborted ? 'đã dừng' : 'lỗi') + '</span>';
      shell.prose.innerHTML = aborted
        ? '<p class="muted">Đã dừng theo yêu cầu.</p>'
        : '<p style="color:var(--err)">' + esc(errText(e)) + '</p>';
      if (!aborted) toast(errText(e), 'err');
    } finally {
      chat.busy = false;
      chat.controller = null;
      setBusy('chat', false);
      scrollThread(chatThread);
    }
  }

  $('#chatSend').addEventListener('click', function () {
    if (chat.busy) { if (chat.controller) chat.controller.abort(); return; }
    sendChat();
  });
  chatInput.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); sendChat(); }
  });

  /* ══════════════════════════════════════════════════════════════════════
     Dropzone dùng chung
     ══════════════════════════════════════════════════════════════════ */
  function wireDropzone(zoneSel, inputSel, onPick) {
    var zone = $(zoneSel), input = $(inputSel);
    zone.addEventListener('click', function () { input.click(); });
    ['dragenter', 'dragover'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) { e.preventDefault(); zone.classList.add('drag'); });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      zone.addEventListener(evt, function (e) { e.preventDefault(); zone.classList.remove('drag'); });
    });
    zone.addEventListener('drop', function (e) {
      var f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) { onPick(f); }
    });
    input.addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0];
      if (f) onPick(f);
    });
  }

  function markDropzone(zoneSel, file) {
    var zone = $(zoneSel);
    zone.classList.add('has-file');
    $('b', zone).textContent = file.name;
    $('small', zone).textContent = fmtBytes(file.size) + ' · bấm để đổi file';
  }

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: SOÁT VĂN BẢN (workflow 2)

     Thứ chính trên màn này là BẢN DỰNG LẠI TÀI LIỆU, không phải danh sách lỗi.
     Một danh sách phẳng bắt người đọc cầm từng dòng đi dò lại trong file gốc -
     xong bản soát thì mệt hơn lúc chưa soát. Ở đây tài liệu hiện đúng phông, cỡ
     chữ, canh lề và lề trang đọc được từ file, lỗi khoanh ngay tại chỗ, bấm vào
     khung là ra lời giải thích và chỗ sửa.

     Danh sách vẫn còn nhưng gập lại - nó để đối chiếu và đếm, không để đọc.
     ══════════════════════════════════════════════════════════════════ */
  var reviewFile = null;
  var ruleSetsLoaded = false;
  wireDropzone('#reviewDrop', '#reviewFile', function (f) { reviewFile = f; markDropzone('#reviewDrop', f); });

  async function loadRuleSets() {
    var sel = $('#reviewRuleSet');
    try {
      var items = await API.ruleSets();
      ruleSetsLoaded = true;
      sel.innerHTML = '';
      items.forEach(function (r) {
        sel.appendChild(el('option', { value: r.id },
          esc(r.label) + (r.version ? ' · ' + esc(r.version) : '')));
      });
      if (!items.length) sel.appendChild(el('option', { value: '' }, 'Không có bộ tiêu chí nào'));
    } catch (e) {
      sel.innerHTML = '';
      sel.appendChild(el('option', { value: '' }, 'Mặc định'));
    }
  }

  var SEVERITY = { error: 'err', warning: 'warn', info: 'info' };
  var SEV_OK = { error: 1, warning: 1, info: 1 };
  var ALIGN_CSS = { LEFT: 'left', CENTER: 'center', RIGHT: 'right', JUSTIFY: 'justify' };

  var TYPE_VI = {
    spelling: 'chính tả', spacing: 'dấu cách', punctuation: 'dấu câu',
    duplicate: 'lặp từ', grammar: 'ngữ pháp', wording: 'diễn đạt',
    logic: 'logic', missing: 'thiếu ý',
  };

  // Tên tiếng Việt của từng tiêu chí; mã nào chưa có tên thì hiện nguyên mã.
  var RULE_VI = {
    'structure.title': 'có tiêu đề', 'structure.heading_levels': 'thứ bậc mục',
    'structure.empty_section': 'mục nào cũng có nội dung',
    'structure.duplicate_heading': 'không trùng tên mục',
    'structure.numbering': 'đánh số liên tục', 'structure.paragraph_length': 'độ dài đoạn',
    'consistency.font': 'phông chữ nhất quán', 'consistency.size_pt': 'cỡ chữ nhất quán',
    'consistency.alignment': 'canh lề nhất quán', 'consistency.line_spacing': 'giãn dòng nhất quán',
    'consistency.paragraph_spacing_pt': 'khoảng cách đoạn nhất quán',
    'page.size_mm': 'khổ giấy', 'page.margin_mm': 'lề trang',
  };

  function ruleVi(item) {
    var cut = item.indexOf(' (');
    var id = cut === -1 ? item : item.slice(0, cut);
    return (RULE_VI[id] || id) + (cut === -1 ? '' : item.slice(cut));
  }

  /* ─────────────── dựng lại tài liệu ───────────────
     Mọi giá trị dưới đây đọc từ file người dùng tải lên, tức là dữ liệu KHÔNG
     tin được y như chuỗi LLM sinh. Một tệp .docx dựng có chủ đích đặt được tên
     phông kiểu `x;background:url(...)`; ghép thẳng vào thuộc tính style là mở
     đúng cái cửa mà `MD.escape` đang đóng ở chỗ khác. Nên: tên phông lọc còn
     chữ-số-cách-gạch, còn số thì ép về number và chặn hai đầu.                */
  function safeFont(name) {
    return String(name || '').replace(/[^A-Za-z0-9 \-]/g, '').trim().slice(0, 48);
  }

  function safeNum(value, min, max) {
    var n = Number(value);
    return (isFinite(n) && n >= min && n <= max) ? n : null;
  }

  var PT_PER_MM = 72 / 25.4;

  // PDF không lưu canh lề của đoạn (`alignment` luôn null), nên nếu chỉ dựa vào
  // nó thì quốc hiệu, số ký hiệu và nơi nhận bị dồn hết về sát trái - văn bản hành
  // chính vốn xếp phần đầu thành hai cột, dựng lại một cột là trông khác hẳn bản gốc.
  //
  // PDF lại có toạ độ thật của từng khối. Dùng nó đặt ĐÚNG VỊ TRÍ NGANG còn chắc
  // hơn là đoán "căn giữa hay căn phải": đoán thì phải dò ra cột chữ trước, mà
  // trang hai cột làm phép dò đó sai ngay từ đầu.
  function hopChu(b, doc) {
    var geo = doc.geometry, bb = b.bbox;
    if (!geo || !bb || bb.length < 4) return null;
    var trai = safeNum(geo.left_mm, 0, 200), rong = safeNum(geo.width_mm, 50, 600);
    var phai = safeNum(geo.right_mm, 0, 200);
    if (trai == null || rong == null || phai == null) return null;

    var mepTrai = trai * PT_PER_MM;
    var vungRong = (rong - trai - phai) * PT_PER_MM;
    var lui = safeNum(bb[0] - mepTrai, -20, vungRong);
    var w = safeNum(bb[2] - bb[0], 1, vungRong * 1.5);
    if (lui == null || w == null) return null;

    // Nới 4%: phông trình duyệt không khớp phông trong PDF từng phần nghìn, khít
    // quá thì chữ cuối rớt xuống dòng. Vẫn kẹp trong vùng chữ để không tràn trang.
    return { lui: Math.max(0, lui), rong: Math.min(w * 1.04, vungRong - Math.max(0, lui)) };
  }

  function blockStyle(b, doc) {
    var css = [];
    var font = safeFont(b.font || doc.default_font);
    // Nháy ĐƠN quanh tên phông. Nháy kép đóng sớm thuộc tính style="..." khi chuỗi
    // này được ghép vào HTML, làm vỡ thẻ và mất hết phần định dạng phía sau -
    // đúng chỗ bản dựng lại mất lý do tồn tại.
    if (font) css.push("font-family:'" + font + "',serif");
    var size = safeNum(b.size_pt != null ? b.size_pt : doc.default_size_pt, 4, 96);
    if (size) css.push('font-size:' + size + 'pt');
    if (b.bold) css.push('font-weight:700');
    if (ALIGN_CSS[b.alignment]) css.push('text-align:' + ALIGN_CSS[b.alignment]);
    var lh = safeNum(b.line_spacing, 0.5, 5);
    if (lh) css.push('line-height:' + lh);
    var before = safeNum(b.space_before_pt, 0, 200);
    var after = safeNum(b.space_after_pt, 0, 200);
    var hop = hopChu(b, doc);
    css.push('margin:' + (before || 0) + 'pt 0 ' + (after || 0) + 'pt ' +
      (hop ? hop.lui.toFixed(1) : '0') + 'pt');
    if (hop) css.push('width:' + hop.rong.toFixed(1) + 'pt');
    return css.join(';');
  }

  function pageStyle(geo) {
    if (!geo) return '';
    var v = function (x, lo, hi, d) { var n = safeNum(x, lo, hi); return (n == null ? d : n) + 'mm'; };
    return '--pg-w:' + v(geo.width_mm, 50, 600, 210) + ';--pg-h:' + v(geo.height_mm, 50, 900, 297) +
      ';--pg-t:' + v(geo.top_mm, 0, 100, 20) + ';--pg-b:' + v(geo.bottom_mm, 0, 100, 20) +
      ';--pg-l:' + v(geo.left_mm, 0, 100, 30) + ';--pg-r:' + v(geo.right_mm, 0, 100, 20);
  }

  function markText(text, items) {
    var out = '', pos = 0;
    items.forEach(function (f) {
      // Bỏ tô khi span không dùng được hoặc chồng lên span trước: lỗi vẫn còn
      // trong danh sách, chỉ là không khoanh được. Tô chồng thì vỡ cả thẻ HTML.
      if (f.start == null || f.start < pos || f.end > text.length || f.end <= f.start) return;
      out += esc(text.slice(pos, f.start));
      out += '<mark class="loi s-' + (SEV_OK[f.severity] ? f.severity : 'warning') +
        (f.source === 'llm' ? ' src-llm' : '') + '" data-fid="' + f._i + '">' +
        esc(text.slice(f.start, f.end)) + '</mark>';
      pos = f.end;
    });
    return out + esc(text.slice(pos));
  }

  function docViewNode(data) {
    var doc = data.document || {};
    var blocks = data.blocks || [];
    var findings = (data.findings || []).map(function (f, i) { f._i = i; return f; });

    var card = el('div', { class: 'card' });
    card.innerHTML = '<div class="card-head"><h3>Tài liệu &amp; vùng lỗi</h3>' +
      '<span class="pill">' + findings.length + ' chỗ</span>' +
      (data.blocks_truncated ? '<span class="pill warn">tài liệu dài, đã cắt bớt</span>' : '') + '</div>';

    if (!blocks.length) {
      card.appendChild(el('div', { class: 'doc-empty' },
        esc('Không đọc được nội dung nào để dựng lại.')));
      return card;
    }

    var bar = el('div', { class: 'doc-bar' });
    bar.innerHTML = '<div class="legend">' +
      '<span class="l-rule"><i></i>đối chiếu được — chắc chắn</span>' +
      '<span class="l-llm"><i></i>LLM gợi ý — cần xác nhận</span></div><div class="spacer"></div>';
    var zoom = el('div', { class: 'doc-zoom' });
    bar.appendChild(zoom);
    card.appendChild(bar);

    // Lỗi cấu trúc/trình bày không chỉ được ký tự nào (lạc phông, mục rỗng…) nên
    // đánh dấu cả khối - bỏ qua thì người đọc không thấy chúng trên bản dựng lại.
    var caKhoi = {};
    (data.rule_check && data.rule_check.findings || []).forEach(function (f) {
      if (!f.block_id) return;
      if (caKhoi[f.block_id] !== 'error') caKhoi[f.block_id] = f.severity;
    });

    var theoKhoi = {};
    findings.forEach(function (f) { (theoKhoi[f.block_id] = theoKhoi[f.block_id] || []).push(f); });
    Object.keys(theoKhoi).forEach(function (k) {
      theoKhoi[k].sort(function (a, b) { return (a.start || 0) - (b.start || 0); });
    });

    var html = blocks.map(function (b) {
      var cls = 'doc-block' + (b.kind === 'table' ? ' tbl' : '');
      if (caKhoi[b.id]) cls += caKhoi[b.id] === 'error' ? ' whole' : ' whole whole-warn';
      var body = markText(b.text, theoKhoi[b.id] || []);
      // `esc` cả chuỗi style: `safeFont`/`safeNum` đã lọc từng giá trị rồi, nhưng
      // chỗ ghép vào HTML thì không được tin vào bước lọc ở xa.
      return '<p class="' + cls + '" style="' + esc(blockStyle(b, doc)) + '" data-bid="' +
        esc(b.id) + '">' + (body || '&nbsp;') + '</p>';
    }).join('');

    var stage = el('div', { class: 'doc-stage' });
    var scaler = el('div', { class: 'doc-scaler' });
    var page = el('div', { class: 'doc-page', style: pageStyle(doc.geometry) }, html);
    scaler.appendChild(page);
    stage.appendChild(scaler);
    card.appendChild(stage);

    // Thu phóng: bản dựng lại rộng đúng khổ giấy thật (A4 ≈ 794px) nên gần như
    // luôn rộng hơn cột kết quả. `transform` không đổi chiều cao chiếm chỗ, phải
    // tự đặt lại, không thì khung cuộn thừa ra đúng phần đã thu nhỏ.
    var scale = 1;
    function apply(v) {
      scale = v;
      scaler.style.transform = 'scale(' + v + ')';
      scaler.style.height = (page.offsetHeight * v) + 'px';
      $$('button', zoom).forEach(function (btn) {
        btn.classList.toggle('on', Math.abs(Number(btn.dataset.z) - v) < 0.005);
      });
    }
    function vuaKhung() {
      var rong = stage.clientWidth - 24;
      return Math.min(1, Math.max(0.35, rong / (page.offsetWidth || 794)));
    }
    [['vừa khung', 0], ['100%', 1], ['125%', 1.25]].forEach(function (pair) {
      var v = pair[1] || vuaKhung();
      var btn = el('button', { type: 'button', dataset: { z: v } }, esc(pair[0]));
      btn.addEventListener('click', function () { apply(Number(btn.dataset.z)); });
      zoom.appendChild(btn);
    });
    // offsetHeight chỉ đúng sau khi node vào DOM, nên đo ở nhịp vẽ kế tiếp.
    requestAnimationFrame(function () {
      var fit = vuaKhung();
      $$('button', zoom)[0].dataset.z = fit;
      apply(fit);
    });

    stage.addEventListener('click', function (ev) {
      var mark = ev.target.closest('mark.loi');
      if (!mark) {
        var pop = stage.querySelector('.loi-pop');
        if (pop && !ev.target.closest('.loi-pop')) { pop.remove(); boCham(stage); }
        return;
      }
      moPop(stage, mark, findings[Number(mark.dataset.fid)]);
    });
    return card;
  }

  function boCham(stage) { $$('mark.loi.on', stage).forEach(function (m) { m.classList.remove('on'); }); }

  function moPop(stage, mark, f) {
    if (!f) return;
    var cu = stage.querySelector('.loi-pop');
    if (cu) cu.remove();
    boCham(stage);
    mark.classList.add('on');

    // Nói đúng kết luận này ở đâu ra: người ký hỏi lại thì phải trả lời được là
    // máy dựa vào đâu, chứ không chỉ "máy bảo thế".
    var nhan = f.source === 'llm' ? 'LLM soát, cần xác nhận' : 'đo được, chắc chắn';
    var pop = el('div', { class: 'loi-pop' },
      '<button class="pop-close" type="button" aria-label="Đóng">×</button>' +
      '<div class="pop-top"><span class="pill ' + (SEVERITY[f.severity] || 'warn') + '">' +
      esc(TYPE_VI[f.type] || f.type) + '</span><span class="pill">' + esc(nhan) + '</span>' +
      '<span class="pill">khối ' + esc(f.block_id) + '</span></div>' +
      '<div class="pop-msg">' + esc(f.message || '(không có mô tả)') + '</div>' +
      (f.suggest ? '<div class="pop-fix">Sửa thành: ' + esc(f.suggest) + '</div>' : ''));
    pop.querySelector('.pop-close').addEventListener('click', function () { pop.remove(); boCham(stage); });
    stage.appendChild(pop);

    var sr = stage.getBoundingClientRect(), mr = mark.getBoundingClientRect();
    var top = mr.bottom - sr.top + stage.scrollTop + 6;
    var left = mr.left - sr.left + stage.scrollLeft;
    pop.style.top = top + 'px';
    pop.style.left = Math.max(8, Math.min(left, stage.scrollLeft + stage.clientWidth - pop.offsetWidth - 8)) + 'px';
  }

  /* ─────────────── bảng phân công + văn bản giao việc ─────────────── */
  function taskTableNode(tasks) {
    var wrap = el('div', { class: 'table-scroll' });
    var html = '<table class="task-table"><thead><tr>' +
      '<th>TT</th><th>Đơn vị thực hiện</th><th>Nội dung nhiệm vụ</th>' +
      '<th>Số liệu cần chuẩn bị</th><th>Thời hạn</th></tr></thead><tbody>';
    tasks.forEach(function (t, i) {
      html += '<tr class="' + (t.in_catalog === false ? 'ngoai' : '') + '">' +
        '<td class="stt">' + (i + 1) + '</td>' +
        '<td class="dept">' + esc(t.department_name || t.department || 'Ngoài danh mục') +
        (t.department ? '<br><small class="muted">' + esc(t.department) + '</small>' : '') +
        (t.in_catalog === false ? '<br><small class="muted">ngoài danh mục</small>' : '') + '</td>' +
        '<td data-mid="task-' + i + '">' + MD.render(t.task || '') + '</td>' +
        '<td>' + ((t.data_needed || []).length
          ? (t.data_needed || []).map(function (d) { return esc(d); }).join('<br>') : '—') + '</td>' +
        '<td class="dl">' + (t.deadline ? esc(t.deadline) : '—') + '</td></tr>';
      sourceRegistry.set('task-' + i, t.refs || []);
    });
    wrap.innerHTML = html + '</tbody></table>';
    return wrap;
  }

  // Marker trích dẫn "[3]" là thứ của màn hình, không phải của văn bản trình ký.
  function boMarker(text) {
    return String(text || '').replace(/\[\d+\]/g, '').replace(/\s{2,}/g, ' ').trim();
  }

  function giaoViecNode(data) {
    var tasks = data.tasks || [];
    var box = el('div', { class: 'card' });
    box.innerHTML = '<div class="card-head"><h3>Soạn văn bản giao nhiệm vụ</h3>' +
      '<span class="pill">' + tasks.length + ' nơi nhận</span></div>' +
      '<div class="card-sub">Đổ đúng bảng trên ra .docx theo thể thức Nghị định 30 để sửa rồi trình ký. ' +
      'Không gọi LLM - lời văn là văn khuôn, bảng lấy nguyên từ trên. ' +
      'Ô để trống thì file in dấu chấm lửng, không tự đặt số ký hiệu hay tên người ký.</div>';

    var form = el('div', { class: 'gv-form' });
    var goiY = data.giao_viec_goi_y === 'quyet_dinh' ? 'quyet_dinh' : 'cong_van';
    form.innerHTML =
      '<label class="wide"><span>Mẫu văn bản</span><select data-f="loai">' +
      '<option value="cong_van"' + (goiY === 'cong_van' ? ' selected' : '') + '>Công văn giao nhiệm vụ' +
      (goiY === 'cong_van' ? ' — gợi ý' : '') + '</option>' +
      '<option value="quyet_dinh"' + (goiY === 'quyet_dinh' ? ' selected' : '') + '>Quyết định giao nhiệm vụ' +
      (goiY === 'quyet_dinh' ? ' — gợi ý' : '') + '</option></select></label>' +
      '<label><span>Cơ quan ban hành</span><input data-f="co_quan" placeholder="VD: Công ty TPV" /></label>' +
      '<label><span>Địa danh</span><input data-f="dia_danh" placeholder="VD: Hà Nội" /></label>' +
      '<label class="wide"><span>Trích yếu (V/v…)</span><input data-f="trich_yeu" /></label>' +
      '<label><span>Số ký hiệu</span><input data-f="so_ky_hieu" placeholder="để trống = …/CV-…" /></label>' +
      '<label><span>Chức vụ người ký</span><input data-f="chuc_vu_ky" placeholder="VD: Giám đốc" /></label>' +
      '<label><span>Họ tên người ký</span><input data-f="nguoi_ky" /></label>' +
      '<label><span>Hạn chung</span><input data-f="deadline" /></label>';
    box.appendChild(form);

    var cls = data.classification;
    $('[data-f="trich_yeu"]', form).value = cls && cls.topic ? String(cls.topic).replace(/_/g, ' ') : '';
    $('[data-f="deadline"]', form).value = data.deadline || '';

    var nut = el('button', { class: 'primary-btn', type: 'button' }, 'Soạn văn bản giao nhiệm vụ');
    nut.style.marginTop = '12px';
    var ra = el('div');
    box.appendChild(nut);
    box.appendChild(ra);

    nut.addEventListener('click', async function () {
      var body = { tasks: tasks, mo_dau: boMarker(data.summary) };
      $$('[data-f]', form).forEach(function (input) { body[input.dataset.f] = input.value.trim(); });
      if (!body.deadline) body.deadline = null;

      nut.disabled = true;
      var chu = nut.textContent;
      nut.textContent = 'Đang soạn…';
      try {
        var res = await API.giaoViec(body);
        ra.innerHTML = '';
        ra.appendChild(artifactsNode([{ file_name: res.file_name, download_url: res.download_url }]));
        toast('Đã soạn xong — tải về rồi sửa lại phần để trống', 'ok');
      } catch (e) {
        toast(errText(e), 'err');
      } finally {
        nut.disabled = false;
        nut.textContent = chu;
      }
    });
    return box;
  }

  /* ─────────────── ghép cả màn hình ─────────────── */
  function renderReview(data) {
    var box = $('#reviewResult');
    box.innerHTML = '';

    if (data.error) {
      box.appendChild(el('div', { class: 'card' }, '<div style="color:var(--err)">' + esc(data.error) + '</div>'));
      return;
    }

    var doc = data.document || {};
    var totals = data.totals || {};
    var head = el('div', { class: 'card' });
    head.innerHTML = '<div class="card-head"><h3>Tổng quan tài liệu</h3>' +
      '<span class="pill">' + esc(doc.source_format || '?') + '</span>' +
      (doc.has_format_info ? '<span class="pill ok">có thông tin định dạng</span>' : '<span class="pill warn">không đọc được định dạng</span>') +
      '</div><div class="stat-grid">' +
      '<div class="stat"><div class="stat-label">Khối</div><div class="stat-value">' + fmtNum(doc.block_count) + '</div></div>' +
      '<div class="stat"><div class="stat-label">Trang</div><div class="stat-value">' + fmtNum(doc.page_count) + '</div></div>' +
      '<div class="stat ' + (totals.errors ? 'err' : 'ok') + '"><div class="stat-label">Lỗi</div><div class="stat-value">' + fmtNum(totals.errors || 0) + '</div></div>' +
      '<div class="stat ' + (totals.warnings ? 'warn' : '') + '"><div class="stat-label">Cảnh báo</div><div class="stat-value">' + fmtNum(totals.warnings || 0) + '</div></div>' +
      '</div>' +
      '<div class="card-sub" style="margin-top:8px">Trong đó <b>' + fmtNum(totals.chac_chan || 0) +
      '</b> chỗ đối chiếu được (chắc chắn) và <b>' + fmtNum(totals.goi_y || 0) + '</b> chỗ LLM gợi ý (cần xác nhận).</div>' +
      (doc.title ? '<div class="card-sub" style="margin-top:10px">Tiêu đề</div><div class="sm">' + esc(doc.title) + '</div>' : '');
    if (doc.outline && doc.outline.length) {
      head.appendChild(block('Dàn ý dò được', doc.outline.length,
        '<div class="outline">' + doc.outline.map(function (h) {
          return '<div class="outline-item" style="padding-left:' + ((h.level - 1) * 14) + 'px" title="' + esc(h.text) + '">' +
            '<b>' + esc(h.block_id) + '</b>' + esc(h.text) + '</div>';
        }).join('') + '</div>', false));
    } else {
      head.appendChild(el('div', { class: 'muted sm' },
        esc('Không dò được mục nào - tài liệu không chia mục hoặc không đánh số.')));
    }
    box.appendChild(head);

    // Thứ chính: tài liệu dựng lại, khoanh sẵn vùng lỗi.
    box.appendChild(docViewNode(data));

    // rule check - lỗi cấu trúc và trình bày, không chỉ được ký tự nào
    var rc = data.rule_check || {};
    var rcCard = el('div', { class: 'card' });
    var statusPill = rc.status === 'done' ? 'ok' : (rc.status === 'partial' ? 'warn' : '');
    var rcHtml = '<div class="card-head"><h3>Cấu trúc &amp; trình bày (rule engine, không dùng LLM)</h3>' +
      (rc.rule_set ? '<span class="pill">' + esc(rc.rule_set) + '</span>' : '') +
      '<span class="pill ' + statusPill + '">' + esc(rc.status || '?') + '</span></div>';
    if (rc.reason) rcHtml += '<div class="card-sub">' + esc(rc.reason) + '</div>';
    if ((rc.findings || []).length) {
      rcHtml += (rc.findings || []).map(function (f) {
        return '<div class="finding"><span class="finding-badge pill ' + (SEVERITY[f.severity] || '') + '">' + esc(f.severity) + '</span>' +
          '<div class="finding-body"><div class="finding-msg">' + esc(f.message) + '</div>' +
          '<div class="finding-meta"><span>' + esc(f.rule) + '</span>' +
          (f.block_id ? '<span>khối ' + esc(f.block_id) + '</span>' : '') +
          (f.actual ? '<span>thực tế: ' + esc(f.actual) + '</span>' : '') +
          (f.expected ? '<span>yêu cầu: ' + esc(f.expected) + '</span>' : '') + '</div>' +
          (f.quote ? '<div class="quote">' + esc(f.quote) + '</div>' : '') + '</div></div>';
      }).join('');
    } else {
      rcHtml += '<div class="muted sm">Không phát hiện lỗi cấu trúc.</div>';
    }
    rcCard.innerHTML = rcHtml;
    if ((rc.passed || []).length) {
      rcCard.appendChild(block('Tiêu chí đạt', rc.passed.length,
        '<div class="tag-row">' + rc.passed.map(function (p) { return '<span class="pill ok">' + esc(ruleVi(p)) + '</span>'; }).join('') + '</div>', false));
    }
    if ((rc.skipped || []).length) {
      rcCard.appendChild(block('Chưa kiểm được', rc.skipped.length,
        '<div class="tag-row">' + rc.skipped.map(function (p) { return '<span class="pill">' + esc(ruleVi(p)) + '</span>'; }).join('') + '</div>', false));
    }
    box.appendChild(rcCard);

    // Danh sách lỗi nội dung: gập lại, để đối chiếu và đếm chứ không để đọc.
    var findings = data.findings || [];
    var lrCard = el('div', { class: 'card' });
    lrCard.innerHTML = '<div class="card-head"><h3>Lỗi nội dung, liệt kê theo khối</h3>' +
      '<span class="pill">' + findings.length + '</span></div>';
    if (!findings.length) {
      lrCard.innerHTML += '<div class="muted sm">Không phát hiện lỗi nội dung nào.</div>';
    } else {
      var theoKhoi = {};
      findings.forEach(function (f) { (theoKhoi[f.block_id] = theoKhoi[f.block_id] || []).push(f); });
      Object.keys(theoKhoi).forEach(function (k) {
        lrCard.appendChild(block('Khối ' + k, theoKhoi[k].length, theoKhoi[k].map(function (f) {
          return '<div class="finding"><span class="finding-badge pill ' + (SEVERITY[f.severity] || 'warn') + '">' +
            esc(TYPE_VI[f.type] || f.type) + '</span>' +
            '<div class="finding-body"><div class="finding-msg">' + esc(f.message || '') +
            (f.source === 'llm' ? ' <span class="pill">LLM gợi ý</span>' : '') + '</div>' +
            '<div class="quote">' + esc(f.quote) + '</div>' +
            (f.suggest ? '<div class="suggest">→ ' + esc(f.suggest) + '</div>' : '') + '</div></div>';
        }).join(''), false));
      });
    }
    box.appendChild(lrCard);

    // nội dung + định tuyến
    var cls = data.classification;
    var sumCard = el('div', { class: 'card' });
    sumCard.innerHTML = '<div class="card-head"><h3>Nội dung &amp; định tuyến</h3>' +
      (cls ? '<span class="pill accent">' + esc(cls.document_type) + '</span>' +
        (cls.topic ? '<span class="pill">' + esc(cls.topic) + '</span>' : '') +
        (cls.confidence ? '<span class="pill">' + Math.round(cls.confidence * 100) + '%</span>' : '') : '') +
      '</div>' +
      (data.summary ? '<div class="prose" data-mid="review-sum">' + MD.render(data.summary) + '</div>' : '<div class="muted sm">Chưa tóm tắt được.</div>') +
      (data.deadline ? '<div class="card-sub" style="margin-top:8px">Hạn chung: <b>' + esc(data.deadline) + '</b></div>' : '');
    if ((data.summary_refs || []).length) {
      sourceRegistry.set('review-sum', data.summary_refs);
      var sb = sourcesBlock('Nguồn của phần tóm tắt', data.summary_refs);
      if (sb) sumCard.appendChild(sb);
    }
    box.appendChild(sumCard);

    if ((data.tasks || []).length) {
      var taskCard = el('div', { class: 'card' });
      taskCard.innerHTML = '<div class="card-head"><h3>Phân công nhiệm vụ</h3>' +
        '<span class="pill">' + data.tasks.length + '</span></div>' +
        '<div class="card-sub">Bấm vào marker [n] trong ô nhiệm vụ để xem nguyên văn đoạn sinh ra nó.</div>';
      taskCard.appendChild(taskTableNode(data.tasks));
      box.appendChild(taskCard);
      box.appendChild(giaoViecNode(data));
    }

    box.appendChild(el('div', { class: 'card' })).appendChild(
      block('JSON đầy đủ', null, '<pre class="json">' + prettyJSON(data) + '</pre>', false));
  }

  $('#reviewRun').addEventListener('click', function () {
    if (!reviewFile) { toast('Chọn file trước đã', 'err'); return; }
    runWorkflow({
      button: this,
      box: $('#reviewResult'),
      label: 'Đang parse file, dựng dàn ý, soát chính tả và chạy rule engine…',
      hint: { text: 'thường 10-30 giây, tuỳ độ dài văn bản', slowAfter: 40 },
      call: function (signal) {
        return API.review(reviewFile, {
          noiGui: $('#reviewNoiGui').value,
          ruleSet: $('#reviewRuleSet').value,
        }, signal);
      },
      render: renderReview,
    });
  });

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: SOẠN BÁO CÁO (workflow 3)
     ══════════════════════════════════════════════════════════════════ */
  function renderSections(container, sections, midPrefix) {
    (sections || []).forEach(function (s, i) {
      var mid = midPrefix + i;
      var card = el('div', { class: 'card', dataset: { mid: mid } });
      sourceRegistry.set(mid, s.refs || []);
      var body = (s.llm_written_cited && s.llm_written_cited.length ? s.llm_written_cited : s.paragraphs || []).join('\n\n');
      card.innerHTML = '<div class="card-head"><h3>' + esc(s.title || s.id) + '</h3>' +
        (s.kind ? '<span class="pill ' + (s.kind === 'llm' ? 'accent' : '') + '">' + esc(s.kind) + '</span>' : '') +
        (s.has_chart ? '<span class="pill info">có biểu đồ</span>' : '') + '</div>' +
        (body ? '<div class="prose" style="font-size:14px">' + MD.render(body) + '</div>' : '') +
        (s.table ? tableHTML(s.table) : '') +
        (s.image_caption ? '<div class="card-sub" style="margin-top:6px">🖼️ ' + esc(s.image_caption) + '</div>' : '');
      var rb = sourcesBlock('Nguồn của mục này', s.refs);
      if (rb) { rb.style.marginTop = '10px'; card.appendChild(rb); }
      container.appendChild(card);
    });
  }

  function downloadCard(title, downloadUrl, fileName, extra) {
    var card = el('div', { class: 'card' });
    card.innerHTML = '<div class="card-head"><h3>' + esc(title) + '</h3>' + (extra || '') + '</div>';
    if (downloadUrl) {
      card.appendChild(artifactsNode([{ file_name: fileName, kind: (fileName.split('.').pop() || ''), download_url: downloadUrl }]));
    } else {
      card.appendChild(el('div', { class: 'muted sm' }, 'Chưa xuất file — xem phần kiểm chứng số liệu bên dưới.'));
    }
    return card;
  }

  function renderDraft(data) {
    var box = $('#draftResult');
    if (data.error) box.appendChild(el('div', { class: 'card' }, '<div style="color:var(--err)">' + esc(data.error) + '</div>'));

    if ((data.missing_input || []).length) {
      box.appendChild(el('div', { class: 'card' },
        '<div class="card-head"><h3>Thiếu thông tin</h3></div><div class="tag-row">' +
        data.missing_input.map(function (m) { return '<span class="pill warn">' + esc(m) + '</span>'; }).join('') +
        '</div><div class="card-sub" style="margin-top:8px">Hệ thống dừng lại thay vì đoán — bổ sung rồi chạy lại.</div>'));
    }

    var meta = '<span class="pill accent">' + esc(data.template_name || data.template || '—') + '</span>' +
      (data.ma_don_vi ? '<span class="pill">' + esc(data.ma_don_vi) + '</span>' : '') +
      (data.nguon === 'tai_lieu'
        ? '<span class="pill info">nguồn: ' + esc((data.source_document || {}).ten_tai_lieu || 'tài liệu') + '</span>'
        : '') +
      validationPill(data.validation) +
      (data.registered_as ? '<span class="pill info">sổ VB: ' + esc(data.registered_as) + '</span>' : '') +
      (data.retry_count ? '<span class="pill warn">viết lại ' + data.retry_count + ' lần</span>' : '');
    box.appendChild(downloadCard('Bản thảo', data.download_url, (data.output_path || '').split('/').pop(), meta));

    /* Nói thẳng mức bảo đảm của nhánh tài liệu. Hai nhánh cho ra hai văn bản
       trông giống hệt nhau, nhưng thứ đứng sau con số thì khác hẳn - người ký
       cần biết mình đang cầm loại nào. */
    if (data.nguon === 'tai_lieu') {
      var sd = data.source_document || {};
      box.appendChild(el('div', { class: 'card' },
        '<div class="card-head"><h3>Nguồn là tài liệu tải lên</h3>' +
        (sd.co_bang_so_lieu ? '<span class="pill">có bảng số liệu</span>'
                            : '<span class="pill warn">không có bảng số liệu</span>') + '</div>' +
        '<div class="card-sub">Số trong báo cáo được kiểm là <b>có xuất hiện nguyên văn</b> trong ' +
        esc(sd.ten_tai_lieu || 'tài liệu') +
        '. Mức này chặn được số bịa, nhưng <b>không</b> chặn được số có thật mà dùng sai chỗ — ' +
        'khác với nhánh CSDL, nơi mỗi con số truy về được một trường dữ liệu.</div>'));
    }

    var v = validationNode(data.validation);
    if (v) box.appendChild(el('div', { class: 'card' })).appendChild(v);

    renderSections(box, data.sections, 'draft-');

    if ((data.regulations || []).length) {
      var reg = el('div', { class: 'card' });
      reg.innerHTML = '<div class="card-head"><h3>Căn cứ quy định (RAG)</h3><span class="pill">' + data.regulations.length + '</span></div>';
      reg.innerHTML += data.regulations.map(function (r, i) {
        return sourceHTML({ id: i + 1, doc_title: r.doc_title, section: r.section, snippet: r.text, score: r.score }, i + 1);
      }).join('');
      box.appendChild(reg);
    }

    var notes = (data.data_notes || []).concat(data.assumptions || []);
    if (notes.length) {
      box.appendChild(el('div', { class: 'card' },
        '<div class="card-head"><h3>Ghi chú &amp; giả định</h3></div>' +
        '<ul style="margin:0;padding-left:1.2em;font-size:13.3px">' +
        notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>'));
    }

    box.appendChild(el('div', { class: 'card' })).appendChild(
      block('JSON đầy đủ', null, '<pre class="json">' + prettyJSON(data) + '</pre>', false));
  }

  /* --- nguồn nội dung: CSDL theo mẫu, hay một tài liệu tải lên --- */
  var draftFile = null;   // {file_id, name, size, pending} - CHỈ một file

  function renderDraftFile() {
    var row = $('#draftAttachments');
    row.innerHTML = '';
    row.hidden = !draftFile;
    if (!draftFile) return;
    var node = el('span', { class: 'attach' + (draftFile.pending ? ' loading' : '') },
      '📎 ' + esc(draftFile.name) +
      (draftFile.size ? ' <span class="muted">' + fmtBytes(draftFile.size) + '</span>' : ''));
    var x = el('button', { class: 'icon-btn', title: 'Bỏ file' },
      '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>');
    x.addEventListener('click', function () { draftFile = null; renderDraftFile(); });
    node.appendChild(x);
    row.appendChild(node);
  }

  /* Một tài liệu, không phải nhiều: nhánh này soạn báo cáo TỪ nội dung một file.
     Muốn gộp nhiều nguồn thì đó là màn Tổng hợp. Chọn file mới thì thay file cũ
     thay vì xếp hàng - đỡ phải giải thích "file nào đang được dùng". */
  wireDropzone('#draftDrop', '#draftFile', async function (file) {
    var entry = { name: file.name, size: file.size, pending: true };
    draftFile = entry;
    renderDraftFile();
    try {
      var res = await API.agentUpload(file, 'upload');
      entry.file_id = res.file_id;
      entry.pending = false;
      renderDraftFile();
      toast('Đã tải lên: ' + res.file_id, 'ok', 2000);
    } catch (e) {
      if (draftFile === entry) draftFile = null;
      renderDraftFile();
      toast('Tải file thất bại: ' + errText(e), 'err');
    }
  });

  $('#draftRun').addEventListener('click', function () {
    var request = $('#draftRequest').value.trim();
    if (!request) { toast('Nhập yêu cầu trước đã', 'err'); return; }
    // Chặn ngay ở giao diện: backend cũng trả `missing_input` đúng như vậy,
    // nhưng bắt người dùng đợi một vòng gọi API chỉ để nghe "thiếu file" thì vô ích.
    if (!(draftFile && draftFile.file_id)) {
      toast(draftFile ? 'File đang tải lên, đợi một chút…' : 'Chọn tài liệu nguồn trước đã', 'err');
      return;
    }
    runWorkflow({
      button: this,
      box: $('#draftResult'),
      label: 'Đang đọc tài liệu, lập dàn ý và viết từng mục…',
      hint: { text: 'thường 20-40 giây', slowAfter: 45 },
      call: function (signal) {
        // Màn này CHỈ soạn từ tài liệu. Nhánh CSDL của cùng endpoint vẫn còn và
        // agent tổng vẫn dùng (ý định `draft`), nhưng không phơi ra đây nữa:
        // lấy số từ CSDL là việc của màn Tổng hợp.
        return API.draft({
          request: request,
          nguon: 'tai_lieu',
          file_id: draftFile.file_id,
          inputs: Object.assign({}, prefs.inputs, collectInputs('draftInputs')),
        }, signal);
      },
      render: renderDraft,
    });
  });

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: TỔNG HỢP (workflow 4)
     ══════════════════════════════════════════════════════════════════ */
  /* Giữ khớp với `METRIC_LABELS` ở backend (app/agents/nodes/report.py). Chỉ tiêu
     chấm công (có mặt/vắng/đi học/nghỉ phép) đã bỏ hẳn từ đợt chuyển sang ERP -
     không còn nguồn số liệu - nên cũng không còn nhãn ở đây.

     "Số loại thiết bị" = số TÊN thiết bị khác nhau, KHÁC "chủng loại" của ERP
     (`Asm_AssetCategories`). Nhãn cũ là "Số chủng loại" nên bảng gộp theo chủng
     loại in "Số chủng loại: 33" ngay trên một cái bảng có 8 dòng. */
  var METRIC_LABELS = {
    total_personnel: 'Tổng nhân sự', new_hires: 'Tuyển mới', resignations: 'Nghỉ việc',
    total_equipment: 'Tổng thiết bị', good: 'Tình trạng tốt',
    needs_attention: 'Cần xử lý', equipment_types: 'Số loại thiết bị',
  };
  /* Chỉ là ĐƯỜNG LÙI cho những bảng API không khai cột (danh sách file đã đọc...).
     Bảng số liệu lấy cột từ `breakdown_columns` - xem `rowsTable`. */
  var COLUMN_LABELS = {
    ma_nhom: 'Mã', ten_nhom: 'Tên',
    ma_don_vi: 'Mã đơn vị', ten_don_vi: 'Đơn vị', nhan_su: 'Nhân sự',
    nhan_su_ky_truoc: 'Kỳ trước', tuyen_moi: 'Tuyển mới', nghi_viec: 'Nghỉ việc',
    ten_thiet_bi: 'Tên thiết bị', so_luong: 'Số lượng', tinh_trang: 'Tình trạng',
    chung_loai: 'Chủng loại', so_dau_muc: 'Số đầu mục',
    // `Asm_Assets.LastModificationTime`: giờ SỬA BẢN GHI, không phải ngày bảo dưỡng.
    cap_nhat_cuoi: 'Cập nhật gần nhất (ERP)',
    don_vi: 'Đơn vị', tep: 'Tệp báo cáo', so_ky_hieu: 'Số ký hiệu',
    so_dong_bang: 'Số dòng bảng', tong_thiet_bi: 'Tổng thiết bị',
    hoat_dong_tot: 'Hoạt động tốt', can_xu_ly: 'Cần xử lý', nguon_file: 'Nguồn',
  };

  /** Mảng dict -> bảng. `columns` là `breakdown_columns` do chính data tool khai.

     Tự suy cột từ khoá của bản ghi đầu tiên chỉ là đường lùi: bản ghi số liệu
     mang cả khoá nội bộ trùng nhau (`ma_nhom` ≡ `ma_don_vi`, `ten_nhom` ≡
     `ten_don_vi`), nên bảng nhân sự từng ra 8 cột trong đó 4 cột là tên khoá thô.
     Chiều gộp nào có những cột nào là việc của tool, không phải của giao diện. */
  function rowsTable(rows, columns, limit) {
    if (!rows || !rows.length) return '';
    var cols = (columns && columns.length)
      ? columns.filter(function (c) { return c && c.key; })
      : Object.keys(rows[0]).map(function (k) { return { key: k }; });
    var shown = rows.slice(0, limit || 30);
    return '<div class="table-scroll"><table><thead><tr>' +
      cols.map(function (c) { return '<th>' + esc(c.label || COLUMN_LABELS[c.key] || c.key) + '</th>'; }).join('') +
      '</tr></thead><tbody>' +
      shown.map(function (r) {
        return '<tr>' + cols.map(function (c) {
          var v = r[c.key];
          return '<td>' + (typeof v === 'number' ? fmtNum(v) : esc(String(v == null ? '' : v))) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>' +
      (rows.length > shown.length ? '<div class="muted sm" style="margin-top:6px">… còn ' + (rows.length - shown.length) + ' dòng, xem ở JSON thô.</div>' : '');
  }

  function metricTiles(metrics) {
    var keys = Object.keys(metrics || {});
    if (!keys.length) return '';
    return '<div class="stat-grid">' + keys.map(function (k) {
      var m = metrics[k] || {};
      var notes = [];
      if (m.delta != null) notes.push((m.delta > 0 ? '+' : '') + fmtNum(m.delta) + (m.delta_pct != null ? ' (' + m.delta_pct + '%)' : '') + ' so kỳ trước');
      if (m.share_pct != null) notes.push('chiếm ' + m.share_pct + '%');
      var tone = m.delta == null ? '' : (k === 'absent' || k === 'needs_attention' ? (m.delta > 0 ? 'warn' : 'ok') : (m.delta < 0 ? 'warn' : ''));
      return '<div class="stat ' + tone + '"><div class="stat-label">' + esc(METRIC_LABELS[k] || k) + '</div>' +
        '<div class="stat-value">' + fmtNum(m.value) + '</div>' +
        (notes.length ? '<div class="stat-note">' + esc(notes.join(' · ')) + '</div>' : '') + '</div>';
    }).join('') + '</div>';
  }

  function renderAggregateData(data) {
    var card = el('div', { class: 'card' });
    card.innerHTML = '<div class="card-head"><h3>Số liệu gốc (data tool)</h3>' +
      '<span class="pill info">mọi con số trong báo cáo truy về đây</span></div>';

    if (data.nguon_so_lieu === 'tai_lieu') {
      var eq = data.equipment || {};
      card.innerHTML += '<div class="card-sub">Đọc thẳng từ báo cáo đơn vị — không lấy từ CSDL</div>' +
        rowsTable((eq.sources || []).map(function (s) {
          return {
            don_vi: s.ten_don_vi || s.ma_don_vi || '—',
            tep: s.file,
            so_ky_hieu: s.so_ky_hieu || '—',
            so_dong_bang: s.so_dong_bang,
            tong_thiet_bi: (s.figures || {}).tong,
          };
        })) +
        ((eq.failed || []).length
          ? '<div class="card-sub" style="margin-top:8px">Không đọc được</div><div class="tag-row">' +
            eq.failed.map(function (f) {
              return '<span class="pill err">' + esc(f.file) + ': ' + esc(f.ly_do) + '</span>';
            }).join('') + '</div>'
          : '') +
        ((eq.notes || []).length
          ? '<div class="card-sub" style="margin-top:8px">Ghi chú khi đọc</div><ul style="margin:0;padding-left:1.2em;font-size:12.8px">' +
            eq.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>'
          : '');
    }

    var rp = data.reporting;
    if (rp && rp.units_total != null) {
      card.innerHTML += '<div class="card-sub">Tình hình gửi báo cáo kỳ ' + esc(rp.period || '') + '</div>' +
        '<div class="stat-grid"><div class="stat ' + (rp.units_reported === rp.units_total ? 'ok' : 'warn') + '">' +
        '<div class="stat-label">Đơn vị đã gửi</div><div class="stat-value">' + rp.units_reported + '/' + rp.units_total + '</div></div></div>' +
        ((rp.missing || []).length ? '<div class="card-sub" style="margin-top:8px">Chưa gửi</div><div class="tag-row">' +
          rp.missing.map(function (m) { return '<span class="pill warn">' + esc(m.ten_don_vi || m.ma_don_vi) + '</span>'; }).join('') + '</div>' : '');
    }

    [['personnel', 'Nhân sự'], ['equipment', 'Trang thiết bị']].forEach(function (pair) {
      var d = data[pair[0]];
      if (!d || !d.metrics) return;
      var scope = d.scope || {};
      /* `units_with_data` chỉ có nghĩa khi gộp theo đơn vị - gộp theo chức vụ hay
         chủng loại thì backend cố ý không trả về, nên không ghép cứng vào chuỗi. */
      var pham_vi = scope.units_with_data != null
        ? ' · ' + scope.units_with_data + '/' + scope.units_requested + ' đơn vị có số liệu'
        : '';
      var body = '<div class="card-sub">Kỳ ' + esc(d.period || '') + ' · đối chiếu ' + esc(d.compare_to || '—') +
        pham_vi + '</div>' +
        metricTiles(d.metrics);
      /* Chỉ tiêu không có nguồn bị BỎ HẲN chứ không trả về 0. Không nói ra thì
         người đọc tưởng "vắng: 0" chứ không phải "không biết". */
      if ((scope.khong_co_chi_tieu || []).length) {
        body += '<div class="card-sub" style="margin-top:8px">Không có số liệu: ' +
          esc(scope.khong_co_chi_tieu.join(', ')) + '</div>';
      }
      if ((d.consistency || []).length) {
        body += '<div class="card-sub" style="margin-top:10px">Lệch ràng buộc nghiệp vụ</div>' +
          d.consistency.map(function (c) {
            // Mỗi mục là {ma_don_vi, message}; in nguyên JSON thì người đọc phải tự bóc.
            return '<div class="quote">' + esc(typeof c === 'string' ? c : (c.message || JSON.stringify(c))) + '</div>';
          }).join('');
      }
      if ((d.breakdown || []).length) {
        var nhom_theo = scope.nhom_theo || 'Đơn vị';
        body += '<div class="card-sub" style="margin-top:12px">Chi tiết theo ' +
          esc(nhom_theo.toLowerCase()) + '</div>' +
          rowsTable(d.breakdown, d.breakdown_columns);
      }
      card.appendChild(block(pair[1], (d.breakdown || []).length || null, body, true));
    });

    card.appendChild(block('Toàn bộ dữ liệu thô (JSON)', null, '<pre class="json">' + prettyJSON(data) + '</pre>', false));
    return card;
  }

  function renderAggregate(data) {
    var box = $('#aggResult');
    if (data.error) box.appendChild(el('div', { class: 'card' }, '<div style="color:var(--err)">' + esc(data.error) + '</div>'));

    var meta = (data.params && data.params.ky ? '<span class="pill accent">kỳ ' + esc(data.params.ky) + '</span>' : '') +
      validationPill(data.validation) +
      (data.has_discrepancy ? '<span class="pill warn">có chênh lệch</span>' : '<span class="pill ok">khớp số kiểm kê</span>') +
      (data.registered_as ? '<span class="pill info">sổ VB: ' + esc(data.registered_as) + '</span>' : '');
    box.appendChild(downloadCard('Báo cáo tổng hợp', data.download_url, (data.output_path || '').split('/').pop(), meta));

    if (data.data && Object.keys(data.data).length) box.appendChild(renderAggregateData(data.data));

    var v = validationNode(data.validation);
    if (v) box.appendChild(el('div', { class: 'card' })).appendChild(v);

    if ((data.reconciliation || []).length) {
      var rc = el('div', { class: 'card' });
      rc.innerHTML = '<div class="card-head"><h3>Đối chiếu báo cáo đơn vị</h3><span class="pill">' + data.reconciliation.length + '</span></div>';
      rc.innerHTML += data.reconciliation.map(function (r) {
        var cls = { matched: 'ok', mismatched: 'err', unreadable: 'warn', no_file: '' }[r.status] || '';
        return '<div class="finding"><span class="finding-badge pill ' + cls + '">' + esc(r.status) + '</span>' +
          '<div class="finding-body"><div class="finding-msg">' + esc(r.ma_don_vi) + '</div>' +
          (r.file_path ? '<div class="finding-meta"><span>' + esc(r.file_path) + '</span></div>' : '') +
          ((r.discrepancies || []).length ? r.discrepancies.map(function (d) {
            return '<div class="quote">' + esc(d.label || d.field) + ': báo cáo ghi <b>' + esc(String(d.file_value)) +
              '</b>, kiểm kê <b>' + esc(String(d.db_value)) + '</b>' + (d.message ? ' — ' + esc(d.message) : '') + '</div>';
          }).join('') : '') + '</div></div>';
      }).join('');
      box.appendChild(rc);
    }

    renderSections(box, data.sections, 'agg-');

    if ((data.assumptions || []).length) {
      box.appendChild(el('div', { class: 'card' },
        '<div class="card-head"><h3>Giả định</h3></div><ul style="margin:0;padding-left:1.2em;font-size:13.3px">' +
        data.assumptions.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>'));
    }

    box.appendChild(el('div', { class: 'card' })).appendChild(
      block('JSON đầy đủ', null, '<pre class="json">' + prettyJSON(data) + '</pre>', false));
  }

  $('#aggRun').addEventListener('click', function () {
    var request = $('#aggRequest').value.trim();
    if (!request) { toast('Nhập yêu cầu trước đã', 'err'); return; }
    runWorkflow({
      button: this,
      box: $('#aggResult'),
      label: 'Đang gộp số liệu nhiều đơn vị từ CSDL, vẽ biểu đồ và đối chiếu file đã gửi…',
      hint: { text: 'thường 20-40 giây', slowAfter: 45 },
      call: function (signal) {
        // Màn này CHỈ lấy số từ CSDL - chỉ CSDL mới có kỳ trước để so tăng/giảm.
        // Nhánh đọc bảng trong báo cáo đơn vị (`nguon_so_lieu=tai_lieu`) vẫn còn
        // ở backend cho ai gọi thẳng API; soạn từ tài liệu là việc của màn Soạn
        // báo cáo.
        var inputs = Object.assign({}, prefs.inputs, collectInputs('aggInputs'), {
          nguon_so_lieu: 'csdl',
        });
        return API.aggregate({ request: request, inputs: inputs }, signal);
      },
      render: renderAggregate,
    });
  });

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: SLIDE (workflow 5)
     ══════════════════════════════════════════════════════════════════ */

  function renderPresentation(data) {
    var box = $('#slideResult');
    if (data.error) box.appendChild(el('div', { class: 'card' }, '<div style="color:var(--err)">' + esc(data.error) + '</div>'));

    var meta = '<span class="pill accent">' + (data.slide_count || 0) + ' slide</span>' +
      (data.engine ? '<span class="pill info">engine: ' + esc(data.engine) + '</span>' : '') +
      (data.elapsed_seconds ? '<span class="pill">' + Math.round(data.elapsed_seconds) + ' giây</span>' : '') +
      validationPill(data.validation);
    box.appendChild(downloadCard('Bộ slide', data.download_url, (data.output_path || '').split('/').pop(), meta));

    /* Bộ slide vẫn nằm bên Presenton sau khi xuất file, sửa tiếp được ở đó.
       `edit_url` là đường dẫn TƯƠNG ĐỐI (`/presentation?id=...`) và backend proxy
       đường đó sang Presenton, nên nó mở được ở bất cứ đâu mở được giao diện này -
       kể cả qua Cloudflare - và vẫn nằm sau chốt token như mọi đường khác. */
    if (data.edit_url) {
      box.appendChild(el('div', { class: 'card' },
        '<div class="card-head"><h3>Sửa tiếp trong Presenton</h3></div>' +
        '<a class="ghost-btn sm" href="' + esc(data.edit_url) + '" target="_blank" rel="noopener">Mở trình sửa slide</a>' +
        '<div class="card-sub" style="margin-top:8px">Sửa xong thì tải lại file từ Presenton — bản .pptx ở trên là bản lúc tạo.</div>'));
    }

    var v = validationNode(data.validation);
    if (v) box.appendChild(el('div', { class: 'card' })).appendChild(v);

    /* Đây là TOÀN BỘ thứ Presenton nhìn thấy. Một con số trên slide mà không có
       trong này thì là bên kia viết thêm, không phải số liệu sai. */
    if (data.brief) {
      box.appendChild(el('div', { class: 'card' })).appendChild(
        block('Số liệu đã gửi cho Presenton', null, '<pre class="json">' + esc(data.brief) + '</pre>', false));
    }
    if ((data.assumptions || []).length) {
      box.appendChild(el('div', { class: 'card' },
        '<div class="card-head"><h3>Giả định</h3></div><ul style="margin:0;padding-left:1.2em;font-size:13.3px">' +
        data.assumptions.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>'));
    }
  }

  $('#slideRun').addEventListener('click', function () {
    var request = $('#slideRequest').value.trim();
    if (!request) { toast('Nhập yêu cầu trước đã', 'err'); return; }
    runWorkflow({
      button: this,
      box: $('#slideResult'),
      label: 'Đang lấy số liệu, soạn nội dung từng slide và chờ Presenton render…',
      hint: { text: 'thường 45-90 giây — Presenton render xong mới trả về', slowAfter: 90 },
      call: function (signal) {
        return API.presentation({
          request: request,
          inputs: Object.assign({}, prefs.inputs, collectInputs('slideInputs')),
        }, signal);
      },
      render: renderPresentation,
    });
  });

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: KHO TRI THỨC
     ══════════════════════════════════════════════════════════════════ */
  var corpusFile = null;
  wireDropzone('#corpusDrop', '#corpusFile', function (f) { corpusFile = f; markDropzone('#corpusDrop', f); });

  async function loadStats() {
    var box = $('#corpusResult');
    var existing = $('#statsCard');
    if (!existing) {
      existing = el('div', { class: 'card', id: 'statsCard' });
      box.innerHTML = '';
      box.appendChild(existing);
    }
    existing.innerHTML = '<div class="card-head"><h3>Collection</h3></div><div class="skeleton" style="width:60%"></div>';
    try {
      var s = await API.stats();
      existing.innerHTML = '<div class="card-head"><h3>Collection</h3><span class="pill accent">' + esc(s.collection) + '</span></div>' +
        '<div class="stat-grid"><div class="stat"><div class="stat-label">Số point</div><div class="stat-value">' + fmtNum(s.points) + '</div></div></div>' +
        '<div class="card-sub" style="margin-top:10px">Vector đang dùng</div><div class="tag-row">' +
        (s.vectors || []).map(function (v) { return '<span class="pill">' + esc(v) + '</span>'; }).join('') + '</div>';
    } catch (e) {
      existing.innerHTML = '<div class="card-head"><h3>Collection</h3><span class="pill err">lỗi</span></div>' +
        '<div class="muted sm">' + esc(errText(e)) + '</div>';
    }
  }

  function pushCorpusResult(html, kind) {
    var box = $('#corpusResult');
    var card = el('div', { class: 'card' }, html);
    if (kind === 'err') card.style.borderColor = 'var(--err)';
    var stats = $('#statsCard');
    if (stats && stats.nextSibling) box.insertBefore(card, stats.nextSibling);
    else box.appendChild(card);
  }

  $('#corpusUpload').addEventListener('click', async function () {
    if (!corpusFile) { toast('Chọn file trước đã', 'err'); return; }
    var btn = this;
    btn.disabled = true;
    var pending = el('div', { class: 'card' }, '');
    pending.appendChild(loaderNode('Đang chuyển sang Markdown, chunk và nhúng vector…'));
    $('#corpusResult').appendChild(pending);
    try {
      var r = await API.corpusUpload(corpusFile, $('#corpusType').value.trim());
      pending.remove();
      pushCorpusResult('<div class="card-head"><h3>Đã nạp: ' + esc(r.doc_title) + '</h3><span class="pill ok">' + r.chunk_count + ' chunk</span></div>' +
        '<div class="card-sub">doc_id: <code>' + esc(r.doc_id) + '</code> · ' + Math.round(r.elapsed_ms) + ' ms' +
        (r.source ? ' · ' + esc(r.source) : '') + '</div>');
      toast('Nạp thành công ' + r.chunk_count + ' chunk', 'ok');
      loadStats();
    } catch (e) {
      pending.remove();
      pushCorpusResult('<div style="color:var(--err)">Nạp thất bại: ' + esc(errText(e)) + '</div>', 'err');
      toast(errText(e), 'err');
    } finally { btn.disabled = false; }
  });

  $('#ingestRun').addEventListener('click', async function () {
    var title = $('#ingestTitle').value.trim();
    var text = $('#ingestText').value.trim();
    if (!title || !text) { toast('Cần cả tiêu đề và nội dung', 'err'); return; }
    var btn = this; btn.disabled = true;
    try {
      var r = await API.ingestText({ text: text, doc_title: title });
      pushCorpusResult('<div class="card-head"><h3>Đã nạp văn bản: ' + esc(r.doc_title) + '</h3><span class="pill ok">' + r.chunk_count + ' chunk</span></div>' +
        '<div class="card-sub">doc_id: <code>' + esc(r.doc_id) + '</code></div>');
      $('#ingestText').value = '';
      toast('Đã nạp văn bản', 'ok');
      loadStats();
    } catch (e) {
      toast(errText(e), 'err');
    } finally { btn.disabled = false; }
  });

  $('#deleteDocRun').addEventListener('click', async function () {
    var id = $('#deleteDocId').value.trim();
    if (!id) { toast('Nhập doc_id', 'err'); return; }
    if (!confirm('Xoá toàn bộ chunk của "' + id + '" khỏi Qdrant?')) return;
    var btn = this; btn.disabled = true;
    try {
      await API.deleteDoc(id);
      toast('Đã xoá ' + id, 'ok');
      $('#deleteDocId').value = '';
      loadStats();
    } catch (e) { toast(errText(e), 'err'); } finally { btn.disabled = false; }
  });

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: TRUY HỒI (debug)
     ══════════════════════════════════════════════════════════════════ */
  function renderSearch(data) {
    var box = $('#searchResult');
    var head = el('div', { class: 'card' });
    head.innerHTML = '<div class="card-head"><h3>Tổng quan</h3><span class="pill accent">' + (data.hits || []).length + ' hit</span>' +
      '<span class="pill">fused ' + data.fused_count + '</span></div>' +
      '<div class="card-sub">Truy vấn đã dùng</div><div class="tag-row">' +
      (data.queries || []).map(function (q) { return '<span class="pill">' + esc(q) + '</span>'; }).join('') + '</div>' +
      '<div class="card-sub" style="margin-top:12px">Số hit mỗi nhánh</div><div class="stat-grid">' +
      Object.keys(data.branch_hits || {}).map(function (k) {
        return '<div class="stat"><div class="stat-label">' + esc(k) + '</div><div class="stat-value">' + fmtNum(data.branch_hits[k]) + '</div></div>';
      }).join('') + '</div>' +
      '<div class="card-sub" style="margin-top:12px">Thời gian (ms)</div><div class="tag-row">' +
      Object.keys(data.timings_ms || {}).map(function (k) {
        return '<span class="pill">' + esc(k) + ': ' + data.timings_ms[k] + '</span>';
      }).join('') + '</div>';
    box.appendChild(head);

    var list = el('div', { class: 'card' });
    list.innerHTML = '<div class="card-head"><h3>Kết quả xếp hạng</h3></div>';
    (data.hits || []).forEach(function (h, i) {
      var ranks = Object.keys(h.branch_ranks || {}).map(function (k) {
        return '<span class="pill">' + esc(k) + ' #' + h.branch_ranks[k] + '</span>';
      }).join('');
      var hit = el('div', { class: 'hit' });
      hit.innerHTML = '<div class="hit-head"><span class="hit-rank">' + (i + 1) + '</span>' +
        '<span class="hit-title">' + esc(h.doc_title || h.doc_id) + '</span>' +
        '<span class="pill ' + (h.matched_by === 'keyword' ? 'warn' : 'accent') + '">rerank ' +
        h.rerank_score.toFixed(3) + '</span>' +
        (h.matched_by === 'keyword'
          ? '<span class="pill warn" title="Dưới ngưỡng rerank nhưng khớp nguyên văn mọi từ khoá của câu hỏi">van cứu từ khoá</span>'
          : '') +
        '<span class="pill">rrf ' + h.rrf_score.toFixed(4) + '</span></div>' +
        '<div class="tag-row" style="margin-bottom:8px">' + ranks +
        (h.section ? '<span class="pill">' + esc(h.section) + '</span>' : '') +
        (h.page != null ? '<span class="pill">trang ' + h.page + '</span>' : '') + '</div>' +
        '<div class="hit-text">' + esc(h.text) + '</div>' +
        '<div class="score-bar"><i style="width:' + Math.max(2, Math.min(100, h.rerank_score * 100)) + '%"></i></div>';
      $('.hit-text', hit).addEventListener('click', function () { this.classList.toggle('open'); });
      list.appendChild(hit);
    });
    if (!(data.hits || []).length) list.appendChild(el('div', { class: 'muted sm' }, 'Không có chunk nào vượt ngưỡng rerank.'));
    box.appendChild(list);
  }

  $('#searchRun').addEventListener('click', function () {
    var query = $('#searchQuery').value.trim();
    if (!query) { toast('Nhập truy vấn', 'err'); return; }
    runWorkflow({
      button: this,
      box: $('#searchResult'),
      label: 'Đang chạy 3 nhánh truy hồi rồi rerank…',
      call: function (signal) {
        return API.search({
          query: query,
          filters: {
            doc_ids: splitList($('#searchDocIds').value),
            sources: [],
            doc_types: splitList($('#searchDocTypes').value),
          },
          top_n: Number($('#searchTopN').value) || 10,
          use_rerank: $('#searchRerank').checked,
          rewrite: $('#searchRewrite').checked,
        }, signal);
      },
      render: renderSearch,
    });
  });

  /* ══════════════════════════════════════════════════════════════════════
     VIEW: CÔNG CỤ & HỆ THỐNG
     ══════════════════════════════════════════════════════════════════ */
  var toolsCache = null;
  async function loadToolsPage() {
    var page = $('#toolsPage');
    var h = lastHealth;
    var healthHTML;
    if (h) {
      healthHTML = '<div class="stat-grid">' +
        [['Trạng thái', h.status, h.status === 'ok' ? 'ok' : 'warn'],
         ['Qdrant', h.qdrant ? 'sống' : 'chết', h.qdrant ? 'ok' : 'err'],
         ['LLM', h.llm ? 'sống' : 'chết', h.llm ? 'ok' : 'err'],
         ['CSDL', h.database ? 'sống' : 'chết', h.database ? 'ok' : 'err'],
         ['Số point', fmtNum(h.points), '']].map(function (s) {
          return '<div class="stat ' + s[2] + '"><div class="stat-label">' + esc(s[0]) + '</div><div class="stat-value" style="font-size:17px">' + esc(String(s[1])) + '</div></div>';
        }).join('') + '</div>' +
        '<div class="card-sub" style="margin-top:12px">Chi tiết</div><div class="tag-row">' +
        Object.keys(h.details || {}).map(function (k) {
          return '<span class="pill">' + esc(k) + ': ' + esc(String(h.details[k])) + '</span>';
        }).join('') + '</div>';
    } else {
      healthHTML = '<div class="muted sm">Không kết nối được backend tại <code>' + esc(API.getBase()) + '</code>.<br>' +
        'Chạy: <code>uv run uvicorn app.main:app --reload --port 8080</code> trong thư mục <code>backend/</code>.</div>';
    }

    page.innerHTML = '';
    var healthCard = el('div', { class: 'card' },
      '<div class="card-head"><h3>Hạ tầng</h3><span class="pill">' + esc(API.getBase()) + '</span></div>' + healthHTML);
    var refresh = el('button', { class: 'ghost-btn sm', style: 'margin-top:12px' }, 'Kiểm tra lại');
    refresh.addEventListener('click', pollHealth);
    healthCard.appendChild(refresh);
    page.appendChild(healthCard);

    var toolCard = el('div', { class: 'card' });
    toolCard.innerHTML = '<div class="card-head"><h3>Danh mục tool của agent</h3></div><div class="card-sub">Tên tool phải có thật, tham số phải hợp lệ — sai là chặn, không đoán ý.</div>';
    page.appendChild(toolCard);

    try {
      if (!toolsCache) toolsCache = await API.tools();
      toolsCache.forEach(function (t) {
        var params = Object.keys(t.parameters || {}).map(function (k) {
          return '<div class="finding-meta"><span><code>' + esc(k) + '</code></span><span>' + esc(t.parameters[k]) + '</span></div>';
        }).join('');
        toolCard.appendChild(block(t.name, null,
          '<div style="font-size:13.2px;margin-bottom:8px">' + esc(t.description) + '</div>' +
          (t.reads_database ? '<span class="pill info">đọc CSDL</span>' : '') + params, false));
      });
    } catch (e) {
      toolCard.appendChild(el('div', { class: 'muted sm' }, 'Không tải được danh mục: ' + esc(errText(e))));
    }

    page.appendChild(el('div', { class: 'card' },
      '<div class="card-head"><h3>Năm workflow</h3></div>' +
      '<div class="prose" style="font-size:13.6px">' + MD.render(
        '| # | Workflow | Cửa vào riêng |\n|---|---|---|\n' +
        '| 1 | Hỏi đáp tài liệu (RAG hybrid) | `POST /api/chat/qa` |\n' +
        '| 2 | Soát cấu trúc + chữ nghĩa + phân rã nhiệm vụ | `POST /api/documents/review` |\n' +
        '| 3 | Soạn văn bản theo mẫu | `POST /api/reports/draft` |\n' +
        '| 4 | Tổng hợp báo cáo nhiều đơn vị | `POST /api/reports/aggregate` |\n' +
        '| 5 | Tạo bộ slide | `POST /api/presentations/create` |\n\n' +
        'Agent tổng (`POST /api/agent/chat`) tự chọn một trong năm nhánh trên; không có file đính kèm thì không bao giờ đi nhánh soát tài liệu.'
      ) + '</div>'));
  }

  /* ══════════════════════════════════════════════════════════════════════
     DRAWER THAM SỐ
     ══════════════════════════════════════════════════════════════════ */
  var drawer = $('#paramsDrawer'), drawerScrim = $('#drawerScrim');
  function openDrawer() { drawer.classList.add('show'); drawerScrim.classList.add('show'); drawer.setAttribute('aria-hidden', 'false'); }
  function closeDrawer() { drawer.classList.remove('show'); drawerScrim.classList.remove('show'); drawer.setAttribute('aria-hidden', 'true'); }
  $('#paramsBtn').addEventListener('click', openDrawer);
  $('#drawerClose').addEventListener('click', closeDrawer);
  drawerScrim.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeDrawer(); hidePopover(); } });

  function kvRow(listId, key, value) {
    var row = el('div', { class: 'kv-row' });
    row.innerHTML = '<input class="kv-key" placeholder="tên trường" value="' + esc(key || '') + '" />' +
      '<input class="kv-val" placeholder="giá trị" value="' + esc(value || '') + '" />';
    var del = el('button', { class: 'icon-btn', title: 'Bỏ trường' },
      '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>');
    del.addEventListener('click', function () {
      row.remove();
      if (listId === 'prefInputs') syncPrefInputs();
    });
    row.appendChild(del);
    if (listId === 'prefInputs') {
      $$('input', row).forEach(function (i) { i.addEventListener('change', syncPrefInputs); });
    }
    $('#' + listId).appendChild(row);
    return row;
  }

  function addPrefInput(key, value) {
    var existing = $$('#prefInputs .kv-row').filter(function (r) { return $('.kv-key', r).value === key; })[0];
    if (existing) { $('.kv-val', existing).focus(); return existing; }
    var row = kvRow('prefInputs', key, value);
    syncPrefInputs();
    return row;
  }

  function syncPrefInputs() {
    prefs.inputs = collectInputs('prefInputs');
    savePrefs();
  }

  $('#prefAddInput').addEventListener('click', function () { kvRow('prefInputs', '', ''); });
  $('#draftAddInput').addEventListener('click', function () { kvRow('draftInputs', '', ''); });
  $('#aggAddInput').addEventListener('click', function () { kvRow('aggInputs', '', ''); });
  $('#slideAddInput').addEventListener('click', function () { kvRow('slideInputs', '', ''); });

  // nạp giá trị đã lưu vào drawer
  $('#apiBase').value = API.getBase();
  $('#apiBase').addEventListener('change', function () {
    var v = API.setBase(this.value);
    this.value = v;
    toolsCache = null;
    pollHealth();
    toast('Đã đổi API base: ' + v, 'ok');
  });
  /* Chọn tài khoản = đổi danh tính demo. Backend tra quyền từ ERP theo
     `X-User-Id`, nên đổi người là đổi cả quyền LẪN tenant - phải nạp lại mọi thứ
     đang hiển thị, y như đổi thuê bao. */
  function moTaTaiKhoan(a) {
    var quyen = a.is_admin ? 'toàn quyền' : a.permission_count + ' quyền';
    return a.display_name + ' — ' + (a.roles.join(', ') || 'không vai trò') + ' (' + quyen + ')';
  }

  async function napDanhSachTaiKhoan() {
    var sel = $('#userPicker');
    if (!sel) return;
    try {
      var d = await API.accounts();
      var dang = API.getUser();
      d.accounts.forEach(function (a) {
        var o = el('option', { value: String(a.user_id) }, moTaTaiKhoan(a));
        if (String(a.user_id) === dang) o.selected = true;
        sel.appendChild(o);
      });
      $('#userPickerNote').textContent =
        'Tenant ' + d.tenant_id + ' · ' + d.accounts.length + ' tài khoản. '
        + 'Quyền đọc từ ERP, không đặt trong ứng dụng này.';
    } catch (e) {
      $('#userPickerNote').textContent = 'Không lấy được danh sách tài khoản: ' + (e.message || e);
    }
  }

  $('#userPicker') && $('#userPicker').addEventListener('change', function () {
    var v = API.setUser(this.value);
    toolsCache = null;
    pollHealth();
    var nhan = this.options[this.selectedIndex].textContent;
    toast(v ? 'Đang dùng danh tính: ' + nhan : 'Bỏ chọn tài khoản, dùng mặc định backend', 'ok');
  });
  napDanhSachTaiKhoan();

  /* Đổi thuê bao là đổi toàn bộ số liệu đang xem, nên xoá cache tool và hỏi lại
     sức khoẻ backend giống hệt lúc đổi API base. */
  $('#tenantId').value = API.getTenant();
  $('#tenantId').addEventListener('change', function () {
    var v = API.setTenant(this.value);
    this.value = v;
    toolsCache = null;
    pollHealth();
    toast(v ? 'Đang xem số liệu thuê bao ' + v : 'Dùng thuê bao mặc định của backend', 'ok');
  });
  $('#prefUnit').value = prefs.unit || '';
  $('#prefUnit').addEventListener('change', function () { prefs.unit = this.value.trim(); savePrefs(); });
  $('#prefTopN').value = prefs.topN || '';
  $('#prefTopN').addEventListener('change', function () { prefs.topN = this.value; savePrefs(); });
  ['docIds', 'docTypes', 'sources'].forEach(function (key) {
    var node = $('#pref' + key.charAt(0).toUpperCase() + key.slice(1));
    node.value = prefs[key] || '';
    node.addEventListener('change', function () { prefs[key] = this.value; savePrefs(); });
  });
  Object.keys(prefs.inputs || {}).forEach(function (k) { kvRow('prefInputs', k, prefs.inputs[k]); });
  if (!Object.keys(prefs.inputs || {}).length) { kvRow('prefInputs', 'nguoi_ky', ''); kvRow('prefInputs', 'chuc_vu_ky', ''); }

  $('#traceToggle').checked = !!prefs.trace;
  $('#traceToggle').addEventListener('change', function () { prefs.trace = this.checked; savePrefs(); });

  $('#prefReset').addEventListener('click', function () {
    localStorage.removeItem(PREFS_KEY);
    API.setTenant('');
    location.reload();
  });

  // mẫu inputs mặc định cho từng workflow
  kvRow('draftInputs', 'nguoi_ky', prefs.inputs.nguoi_ky || '');
  kvRow('draftInputs', 'chuc_vu_ky', prefs.inputs.chuc_vu_ky || '');
  kvRow('aggInputs', 'nguoi_ky', prefs.inputs.nguoi_ky || '');
  kvRow('slideInputs', 'nguoi_trinh_bay', '');

  /* ═══════════════════════ VIEW: SƠ ĐỒ TƯ DUY ════════════════════════
   * Một tài liệu, một đường đi: thả file vào là nạp kho rồi dựng cây.
   *
   * Hai nhịp, đúng như backend: dựng cây (chỉ tiêu đề) rồi bấm từng mục
   * mới xin nội dung. Nội dung đã xin một lần thì giữ trong `mm.sections`,
   * bấm lại là mở ngay - backend cũng cache, nhưng không việc gì phải đi
   * một vòng mạng để nhận lại đúng thứ vừa nhận.
   * ================================================================= */
  var mm = {
    selected: '',    // doc_id của tài liệu đang xem
    title: '',       // tên tài liệu, để hỏi lại trước khi xoá
    data: null,      // phản hồi /generate gần nhất, để vẽ lại khi đổi chế độ
    sections: {},    // node_id -> nội dung đã xin
    collapsed: {},   // node_id -> nhánh người dùng thu lại, ở chế độ danh sách
    view: 'list',    // 'list' | 'graph'
    gclosed: {},     // như trên nhưng của chế độ đồ thị - hai chế độ gập khác nhau
    gInit: '',       // doc_id đã khởi tạo trạng thái gập cho đồ thị
    cam: null,       // {x, y, k} góc nhìn của đồ thị
  };

  /** Hai nút chỉ có nghĩa khi đã có một sơ đồ trên màn hình. */
  function mmSyncControls() {
    $('#mmActions').hidden = !mm.selected;
  }

  /* ── cây ─────────────────────────────────────────────────────────── */
  function mmNodeEl(node, depth) {
    var item = el('li', { class: 'mm-item', dataset: { id: node.id } });
    var kids = node.children || [];
    var head = el('div', { class: 'mm-node lv' + Math.min(depth, 4) });

    if (kids.length) {
      var caret = el('button', {
        class: 'mm-caret', type: 'button',
        'aria-label': 'Thu gọn / mở nhánh',
      }, '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
         'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>');
      caret.addEventListener('click', function (ev) {
        ev.stopPropagation();
        mm.collapsed[node.id] = !mm.collapsed[node.id];
        item.classList.toggle('is-collapsed', !!mm.collapsed[node.id]);
      });
      head.appendChild(caret);
    } else {
      head.appendChild(el('span', { class: 'mm-caret placeholder' }));
    }

    var label = el('button', { class: 'mm-label', type: 'button' },
      '<span class="mm-text">' + esc(node.title) + '</span>' +
      (kids.length ? '<span class="mm-count">' + kids.length + '</span>' : '') +
      (node.has_content ? '<span class="mm-dot" title="Đã có nội dung"></span>' : ''));
    label.addEventListener('click', function () { mmToggleSection(node, item, label); });
    head.appendChild(label);

    item.appendChild(head);

    if (kids.length) {
      var sub = el('ul', { class: 'mm-children' });
      kids.forEach(function (child) { sub.appendChild(mmNodeEl(child, depth + 1)); });
      item.appendChild(sub);
    }
    if (mm.collapsed[node.id]) item.classList.add('is-collapsed');
    return item;
  }

  function mmRender(data) {
    mm.data = data;
    // Chế độ đồ thị mở sẵn gốc + cấp 1, các cấp sâu hơn gập lại: cây 32 mục bung
    // hết một lượt thì không còn là sơ đồ nữa, mà là một bức tường chữ.
    if (mm.gInit !== data.doc_id) {
      mm.gclosed = {};
      (function gap(node, depth) {
        if (depth >= 1 && (node.children || []).length) mm.gclosed[node.id] = true;
        (node.children || []).forEach(function (c) { gap(c, depth + 1); });
      })(data.tree, 0);
      mm.gInit = data.doc_id;
      mm.cam = null;
    }
    mmPaint();
  }

  /** Vẽ lại cả khung kết quả theo chế độ đang chọn. */
  function mmPaint() {
    var data = mm.data;
    if (!data) return;
    var box = $('#mmResult');
    box.innerHTML = '';

    var head = el('div', { class: 'card mm-head' },
      '<div class="card-head"><h3>' + esc(data.doc_title) + '</h3>' +
      '<span class="pill accent">' + fmtNum(data.node_count) + ' mục</span>' +
      (data.cached ? '<span class="pill">bản đã lưu</span>' : '<span class="pill ok">vừa dựng</span>') +
      '</div>' +
      '<div class="mm-modes" role="group" aria-label="Kiểu hiển thị">' +
        '<button type="button" class="mm-mode' + (mm.view === 'graph' ? '' : ' is-active') + '" data-mode="list">' +
          '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>' +
          'Danh sách</button>' +
        '<button type="button" class="mm-mode' + (mm.view === 'graph' ? ' is-active' : '') + '" data-mode="graph">' +
          '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="9.5" width="6.5" height="5" rx="1.5"/><rect x="15.5" y="3" width="6.5" height="5" rx="1.5"/><rect x="15.5" y="16" width="6.5" height="5" rx="1.5"/><path d="M8.5 12h2.5c.8 0 1.5-.7 1.5-1.5v-3c0-.8.7-1.5 1.5-1.5h1M8.5 12h2.5c.8 0 1.5.7 1.5 1.5v3c0 .8.7 1.5 1.5 1.5h1"/></svg>' +
          'Đồ thị</button>' +
      '</div>' +
      '<div class="card-sub">' + (mm.view === 'graph'
        ? 'Bấm một nhánh để mở các mục bên trong nó. Lăn chuột để phóng to, kéo nền để di chuyển.'
        : 'Bấm vào một mục để hệ thống truy hồi trong đúng tài liệu này rồi viết nội dung. ') +
      (data.saved_at ? 'Lưu lúc ' + esc(data.saved_at.replace('T', ' ')) + '.' : '') + '</div>');
    box.appendChild(head);

    $$('.mm-mode', head).forEach(function (b) {
      b.addEventListener('click', function () {
        if (mm.view === b.dataset.mode) return;
        mm.view = b.dataset.mode;
        mmPaint();
      });
    });

    if (mm.view === 'graph') mmPaintGraph(box);
    else {
      var canvas = el('div', { class: 'mm-canvas' });
      var root = el('ul', { class: 'mm-tree' });
      root.appendChild(mmNodeEl(data.tree, 0));
      canvas.appendChild(root);
      box.appendChild(canvas);
    }
  }

  /* ── chế độ đồ thị ───────────────────────────────────────────────────
   * Bố cục cây nằm ngang, tự tính chứ không kéo thư viện về: trang này phải
   * chạy được cả khi máy không ra được Internet, mà phần việc thì gọn - một
   * lượt hậu thứ tự gán chỗ cho từng nhánh.
   *
   * Hộp là <div> thật chứ không phải <text> trong SVG: tiêu đề tiếng Việt cần
   * xuống dòng, mà SVG thì không tự ngắt dòng. SVG chỉ nằm dưới để vẽ cạnh.
   * ================================================================= */
  var G_W = [230, 205, 185, 172, 165];   // bề rộng hộp theo cấp
  var G_HGAP = 58;                       // cách ngang giữa hai cấp
  var G_VGAP = 13;                       // cách dọc giữa hai nhánh cùng cha
  var G_PAD = 26;

  function gWidth(depth) { return G_W[Math.min(depth, G_W.length - 1)]; }
  function gLeft(depth) {
    var x = G_PAD;
    for (var d = 0; d < depth; d++) x += gWidth(d) + G_HGAP;
    return x;
  }

  function svgEl(tag, attrs) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function mmPaintGraph(box) {
    var host = el('div', { class: 'mm-graph' });
    host.appendChild(el('div', { class: 'mm-graph-tools' },
      '<button type="button" class="icon-btn" data-zoom="out" title="Thu nhỏ" aria-label="Thu nhỏ">' +
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/></svg></button>' +
      '<button type="button" class="icon-btn" data-zoom="in" title="Phóng to" aria-label="Phóng to">' +
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button>' +
      '<button type="button" class="icon-btn" data-zoom="fit" title="Vừa khung" aria-label="Vừa khung">' +
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3H3v6M15 3h6v6M9 21H3v-6M15 21h6v-6"/></svg></button>'));

    var stage = el('div', { class: 'mm-stage' });
    host.appendChild(stage);
    box.appendChild(host);

    // Mỗi lần vào lại chế độ đồ thị thì canh cho vừa khung. Còn khi chỉ mở/đóng
    // một nhánh thì `mmDrawGraph` giữ nguyên góc nhìn - đang xem kỹ một nhánh mà
    // sơ đồ tự nhảy về vị trí khác là mất chỗ đang đọc.
    mm.cam = null;
    mmDrawGraph(host, stage);
    mmWireCamera(host, stage);
  }

  /** Dựng lại hộp + cạnh. Giữ nguyên góc nhìn hiện tại (mm.cam). */
  function mmDrawGraph(host, stage) {
    stage.innerHTML = '';
    var svg = svgEl('svg', { class: 'mm-edges' });
    stage.appendChild(svg);

    // 1. Những mục đang nhìn thấy, theo đúng quan hệ cha-con.
    var items = [];
    (function walk(node, depth, parent) {
      var item = { node: node, depth: depth, kids: [] };
      items.push(item);
      if (parent) parent.kids.push(item);
      if ((node.children || []).length && !mm.gclosed[node.id]) {
        node.children.forEach(function (c) { walk(c, depth + 1, item); });
      }
    })(mm.data.tree, 0, null);

    // 2. Dựng hộp trước để ĐO được chiều cao thật - tiêu đề dài ngắn khác nhau
    //    thì hộp cao thấp khác nhau, gán chỗ theo chiều cao đoán mò là chồng nhau.
    items.forEach(function (it) {
      var con = (it.node.children || []).length;
      var dong = con && mm.gclosed[it.node.id];
      var box2 = el('button', {
        class: 'mm-gnode lv' + Math.min(it.depth, 3) + (con ? ' has-kids' : '') + (dong ? ' is-closed' : ''),
        type: 'button',
        title: it.node.title,
      },
        '<span class="mm-gtext">' + esc(it.node.title) + '</span>' +
        (con ? '<span class="mm-gbadge">' + (dong ? '+' : '−') + con + '</span>' : ''));
      box2.style.width = gWidth(it.depth) + 'px';
      box2.style.left = gLeft(it.depth) + 'px';
      if (con) {
        box2.addEventListener('click', function () {
          mm.gclosed[it.node.id] = !mm.gclosed[it.node.id];
          mmDrawGraph(host, stage);
        });
      }
      stage.appendChild(box2);
      it.el = box2;
      it.w = gWidth(it.depth);
    });
    items.forEach(function (it) { it.h = it.el.offsetHeight; });

    // 3. Gán chỗ theo chiều dọc: lá xếp nối nhau, cha đứng giữa đàn con.
    mmLayout(items[0], G_PAD);

    // 4. Đặt hộp và vẽ cạnh.
    var maxX = 0, maxY = 0;
    items.forEach(function (it) {
      it.el.style.top = Math.round(it.y - it.h / 2) + 'px';
      maxX = Math.max(maxX, gLeft(it.depth) + it.w);
      maxY = Math.max(maxY, it.y + it.h / 2);
    });

    items.forEach(function (it) {
      it.kids.forEach(function (kid) {
        var x1 = gLeft(it.depth) + it.w, y1 = it.y;
        var x2 = gLeft(kid.depth), y2 = kid.y;
        // Bezier với hai tay nắm nằm ngang: cạnh rời hộp cha theo phương ngang
        // và cắm vào hộp con cũng theo phương ngang, nên chỗ nối không gãy góc.
        var dx = Math.max(22, (x2 - x1) * 0.5);
        svg.appendChild(svgEl('path', {
          class: 'mm-edge lv' + Math.min(it.depth, 3),
          d: 'M' + x1 + ' ' + y1 + ' C' + (x1 + dx) + ' ' + y1 + ', ' + (x2 - dx) + ' ' + y2 + ', ' + x2 + ' ' + y2,
        }));
      });
    });

    var W = maxX + G_PAD, H = maxY + G_PAD;
    stage.style.width = W + 'px';
    stage.style.height = H + 'px';
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    stage.dataset.w = W;
    stage.dataset.h = H;

    if (!mm.cam) mmFit(host, stage);
    else mmApplyCam(stage);
  }

  /** Lá xếp nối nhau; cha đứng giữa con đầu và con cuối. Trả về chiều cao khối. */
  function mmLayout(item, top) {
    if (!item.kids.length) {
      item.y = top + item.h / 2;
      return item.h;
    }
    var cur = top;
    item.kids.forEach(function (kid) { cur += mmLayout(kid, cur) + G_VGAP; });
    var span = cur - G_VGAP - top;
    item.y = (item.kids[0].y + item.kids[item.kids.length - 1].y) / 2;

    // Cha cao hơn cả đàn con (tiêu đề dài, con thì một mục ngắn): đẩy con xuống
    // cho cân, không thì hộp cha thò ra ngoài khối của chính nó và đè hàng xóm.
    if (item.h > span) {
      var dy = (item.h - span) / 2;
      item.kids.forEach(function (kid) { mmShift(kid, dy); });
      item.y += dy;
      return item.h;
    }
    return span;
  }

  function mmShift(item, dy) {
    item.y += dy;
    item.kids.forEach(function (kid) { mmShift(kid, dy); });
  }

  /* ── phóng to / kéo nền ─────────────────────────────────────────────── */
  function mmApplyCam(stage) {
    stage.style.transform = 'translate(' + mm.cam.x + 'px,' + mm.cam.y + 'px) scale(' + mm.cam.k + ')';
  }

  function mmFit(host, stage) {
    var W = Number(stage.dataset.w) || 1, H = Number(stage.dataset.h) || 1;
    var k = Math.min(1, (host.clientWidth - 16) / W, (host.clientHeight - 16) / H);
    k = Math.max(k, 0.25);
    mm.cam = { k: k, x: Math.max(0, (host.clientWidth - W * k) / 2), y: Math.max(0, (host.clientHeight - H * k) / 2) };
    mmApplyCam(stage);
  }

  function mmWireCamera(host, stage) {
    $$('[data-zoom]', host).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var kind = btn.dataset.zoom;
        if (kind === 'fit') { mmFit(host, stage); return; }
        // Phóng quanh TÂM khung nhìn, không phải quanh gốc toạ độ - nếu không
        // thì mỗi lần bấm + là sơ đồ trôi ra khỏi màn hình.
        mmZoomAt(stage, host.clientWidth / 2, host.clientHeight / 2, kind === 'in' ? 1.2 : 1 / 1.2);
      });
    });

    host.addEventListener('wheel', function (e) {
      e.preventDefault();
      var r = host.getBoundingClientRect();
      mmZoomAt(stage, e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });

    var keo = null;
    host.addEventListener('pointerdown', function (e) {
      if (e.target.closest('.mm-gnode, .mm-graph-tools')) return;
      keo = { x: e.clientX - mm.cam.x, y: e.clientY - mm.cam.y };
      host.classList.add('is-panning');
      host.setPointerCapture(e.pointerId);
    });
    host.addEventListener('pointermove', function (e) {
      if (!keo) return;
      mm.cam.x = e.clientX - keo.x;
      mm.cam.y = e.clientY - keo.y;
      mmApplyCam(stage);
    });
    ['pointerup', 'pointercancel'].forEach(function (evt) {
      host.addEventListener(evt, function () { keo = null; host.classList.remove('is-panning'); });
    });
  }

  function mmZoomAt(stage, px, py, factor) {
    var k = Math.min(2.5, Math.max(0.2, mm.cam.k * factor));
    // Giữ nguyên điểm đang nằm dưới con trỏ: quy nó về toạ độ sơ đồ rồi đặt lại.
    var gx = (px - mm.cam.x) / mm.cam.k;
    var gy = (py - mm.cam.y) / mm.cam.k;
    mm.cam = { k: k, x: px - gx * k, y: py - gy * k };
    mmApplyCam(stage);
  }

  /* ── nội dung một mục ────────────────────────────────────────────── */
  function mmSectionHTML(section) {
    return '<div class="mm-detail-head">' + esc(section.title) +
        (section.cached ? '<span class="pill">đã lưu</span>' : '<span class="pill ok">vừa viết</span>') +
      '</div>' +
      '<div class="prose">' + MD.render(section.summary || '') + '</div>' +
      ((section.key_points || []).length
        ? '<ul class="mm-points">' + section.key_points.map(function (p) {
            return '<li>' + MD.inline(p) + '</li>';
          }).join('') + '</ul>'
        : '') +
      ((section.sources || []).length
        ? '<div class="mm-sources"><span class="mm-sources-label">Nguồn</span>' +
          section.sources.map(function (s) {
            return '<span class="pill">' + esc(s) + '</span>';
          }).join('') + '</div>'
        : '');
  }

  async function mmToggleSection(node, item, label) {
    var open = $('.mm-detail', item);
    // Chỉ lấy khối chi tiết của CHÍNH mục này: `$` tìm cả trong nhánh con, nên
    // phải kiểm tra cha trực tiếp, không thì bấm mục cha lại đóng mục con.
    if (open && open.parentNode !== item) open = null;
    if (open) {
      open.remove();
      label.classList.remove('is-open');
      return;
    }

    label.classList.add('is-open');
    var detail = el('div', { class: 'mm-detail' });
    // Chèn ngay sau tiêu đề, trước danh sách con - nội dung thuộc về mục này.
    item.insertBefore(detail, item.children[1] || null);

    if (mm.sections[node.id]) {
      // Mở lại từ bộ nhớ của trang: nhãn phải là "đã lưu", không phải "vừa viết"
      // của lần xin đầu tiên - lần này có gọi backend đâu.
      detail.innerHTML = mmSectionHTML(Object.assign({}, mm.sections[node.id], { cached: true }));
      return;
    }

    var loader = loaderNode('Đang truy hồi và viết nội dung cho “' + node.title + '”…');
    detail.appendChild(loader);
    try {
      var section = await API.mindmapSection(mm.selected, node.id);
      mm.sections[node.id] = section;
      loader.stop();
      detail.innerHTML = mmSectionHTML(section);
      // Chấm xanh: lần sau bấm vào mục này là mở ngay, không phải chờ.
      if (!$('.mm-dot', label)) label.appendChild(el('span', { class: 'mm-dot', title: 'Đã có nội dung' }));
    } catch (e) {
      loader.stop();
      detail.innerHTML = '<div class="mm-detail-err">' + esc(errText(e)) + '</div>';
    }
  }

  /* ── hành động ───────────────────────────────────────────────────── */

  /* Thả file chỉ là CHỌN file - không gọi mạng. Nạp kho và dựng cây đều nằm sau
     nút, giống mọi màn khác trên giao diện này (Soát tài liệu, Kho tri thức):
     chọn file xong còn kịp đổi ý, và không có lượt LLM nào chạy vì một cú thả
     nhầm tay. */
  var mmFile = null;
  wireDropzone('#mmDrop', '#mmFile', function (file) {
    mmFile = file;
    markDropzone('#mmDrop', file);
    $('#mmRun').disabled = false;
    $('#mmResult').innerHTML = '<div class="result-empty">Đã chọn <b>' + esc(file.name) +
      '</b>. Bấm <b>Dựng sơ đồ tư duy</b> để nạp vào kho rồi dựng cây.</div>';
  });

  $('#mmRun').addEventListener('click', async function () {
    if (!mmFile) { toast('Chọn file trước đã', 'err'); return; }
    var btn = this;
    var box = $('#mmResult');
    box.innerHTML = '';
    var card = el('div', { class: 'card' }, '');
    var loader = loaderNode(
      'Đang nạp “' + mmFile.name + '” vào kho…', null,
      { text: 'Quy về Markdown → chunk → nhúng vector. File scan phải OCR nên lâu hơn.',
        slowAfter: 120 });
    card.appendChild(loader);
    box.appendChild(card);
    btn.disabled = true;
    $('#mmActions').hidden = true;

    try {
      var nap = await API.corpusUpload(mmFile, '');
      loader.stop();
      toast('Đã nạp ' + nap.chunk_count + ' chunk', 'ok');
      mm.selected = nap.doc_id;
      mm.title = nap.doc_title || mmFile.name;
      mm.sections = {};
      mm.collapsed = {};
      // doc_id băm từ nội dung: nạp lại đúng file cũ ra đúng id cũ, nên nếu đã
      // dựng sơ đồ lần trước thì lời gọi dưới trả luôn bản đã lưu, không tốn
      // lượt LLM nào. Vì thế ở đây KHÔNG dựng lại.
      mmBuild(false, btn);
    } catch (e) {
      loader.stop();
      btn.disabled = false;
      mmErrorCard('Không nạp được tài liệu', e);
    }
  });

  function mmErrorCard(tieu_de, e) {
    $('#mmResult').innerHTML = '<div class="card" style="border-color:var(--err)">' +
      '<div class="card-head"><h3>' + esc(tieu_de) + '</h3><span class="pill err">lỗi</span></div>' +
      '<div class="muted sm">' + esc(errText(e)) + '</div></div>';
    toast(errText(e), 'err');
    mmSyncControls();
  }

  function mmBuild(again, button) {
    if (!mm.selected) return;
    runWorkflow({
      button: button || $('#mmRebuild'),
      box: $('#mmResult'),
      // Dựng cây là 5-9 lượt LLM chạy nối nhau, vài chục giây là bình thường -
      // nói trước để không ai tưởng màn hình treo.
      label: 'Đang đọc cả tài liệu theo từng mẻ rồi gộp thành cây chủ đề…',
      hint: { text: 'MAP từng mẻ → REDUCE thành một cây', slowAfter: 120 },
      call: function (signal) {
        return API.mindmapGenerate({ doc_id: mm.selected, regenerate: !!again }, signal);
      },
      render: function (data) {
        mm.sections = {};
        mm.collapsed = {};
        mm.title = data.doc_title || mm.title;
        mmRender(data);
        mmSyncControls();
        toast(data.cached ? 'Mở sơ đồ đã lưu' : ('Đã dựng sơ đồ ' + data.node_count + ' mục'), 'ok');
      },
    });
  }

  $('#mmRebuild').addEventListener('click', function () {
    if (!confirm('Dựng lại sơ đồ của "' + mm.title + '" từ đầu? Bản đang có sẽ bị thay, và việc này tốn 5-9 lượt LLM.')) return;
    mmBuild(true, this);
  });

  $('#mmDelete').addEventListener('click', async function () {
    if (!mm.selected) return;
    if (!confirm('Xoá sơ đồ tư duy của "' + mm.title + '"? Dựng lại sẽ tốn lượt LLM.')) return;
    try {
      await API.mindmapDelete(mm.selected);
      mm.selected = '';
      mm.sections = {};
      mm.collapsed = {};
      mmSyncControls();
      $('#mmResult').innerHTML = '<div class="result-empty">Đã xoá sơ đồ.' +
        (mmFile ? ' Bấm <b>Dựng sơ đồ tư duy</b> để dựng lại từ file đang chọn.' :
                  ' Thả tài liệu vào ô bên trái để dựng lại.') + '</div>';
      toast('Đã xoá sơ đồ', 'ok');
    } catch (e) {
      toast(errText(e), 'err');
    }
  });

  /* ═══════════════════════ VIEW: OCR TÀI LIỆU ════════════════════════
   * Ảnh trang -> Markdown, đọc bằng chính model đang phục vụ cả hệ thống.
   *
   * Màn này nhận trang qua SSE chứ không đợi một phản hồi duy nhất: tài
   * liệu 30 trang scan mất vài phút, và đường công khai qua Cloudflare cắt
   * mọi request im lặng quá 125 giây. Ô trang được dựng sẵn đủ số ngay khi
   * biết tài liệu dày bao nhiêu, rồi điền dần - nên trang về không đúng thứ
   * tự cũng không làm nhảy bố cục.
   * ================================================================= */
  var ocr = {
    file: null,
    tong: 0,        // số trang của tài liệu
    trang: {},      // so_trang -> payload từ backend
    xong: 0,        // số trang đã điền
    meta: null,     // payload của `done`
    fileId: '',     // file đã nằm trên server - đọc lại khỏi tải lên lần nữa
    cheDo: 'auto',  // chế độ của lần chạy đang hiện trên màn hình
    tho: false,     // đang xem Markdown thô thay vì bản dựng
    ctrl: null,
    daNapStatus: false,
  };

  /** Cấu hình OCR: chủ yếu để nói rõ MODEL NÀO đang đọc, và báo sớm nếu tắt. */
  async function ocrLoadStatus() {
    if (ocr.daNapStatus) return;
    ocr.daNapStatus = true;
    try {
      var st = await API.ocrStatus();
      $('#ocrModel').textContent = st.model || 'model đang chạy';
      if (!st.enabled) {
        $('#ocrRun').disabled = true;
        $('#ocrResult').innerHTML = '<div class="card" style="border-color:var(--warn)">' +
          '<div class="card-head"><h3>OCR đang tắt</h3><span class="pill warn">không chạy được</span></div>' +
          '<div class="muted sm">Backend đang đặt <code>OCR_ENABLED=false</code>. Bật lại trong ' +
          '<code>backend/.env</code> rồi khởi động lại uvicorn.</div></div>';
      }
    } catch (e) {
      // Không chặn màn hình vì một dòng chú thích: nút vẫn bấm được, lỗi thật
      // (nếu có) sẽ hiện ra lúc chạy, kèm thông điệp của chính backend.
      ocr.daNapStatus = false;
    }
  }

  var OCR_NGUON = {
    ocr: ['mô hình đọc', 'ok'],
    digital: ['lớp text', ''],
    trong: ['trang trắng', 'muted'],
    loi: ['đọc hỏng', 'err'],
  };

  /** Markdown của cả tài liệu, ghép đúng thứ tự trang - dùng để chép và tải về. */
  function ocrMarkdown() {
    var phan = [];
    for (var i = 1; i <= ocr.tong; i++) {
      var t = ocr.trang[i];
      phan.push(t && t.markdown ? t.markdown.trim() : '');
    }
    if (phan.length <= 1) return phan[0] || '';
    return phan.join('\n\n---\n\n').trim();
  }

  function ocrPageNode(so) {
    var node = el('article', { class: 'ocr-page', dataset: { page: String(so) } });
    node.appendChild(el('header', { class: 'ocr-page-head' },
      '<span class="ocr-page-no">Trang ' + so + '</span><span class="ocr-page-src"></span>'));
    node.appendChild(el('div', { class: 'prose ocr-page-body' },
      '<div class="ocr-waiting"><span class="spinner sm"></span>đang đọc…</div>'));
    return node;
  }

  function ocrFillPage(payload) {
    var node = $('.ocr-page[data-page="' + payload.so_trang + '"]', $('#ocrPages'));
    if (!node) return;
    var nguon = OCR_NGUON[payload.nguon] || [payload.nguon, ''];
    node.classList.add('is-done');
    node.classList.toggle('is-empty', !payload.markdown);
    $('.ocr-page-src', node).innerHTML =
      '<span class="pill ' + nguon[1] + '">' + esc(nguon[0]) + '</span>' +
      (payload.so_ky_tu ? '<span class="ocr-chars">' + fmtNum(payload.so_ky_tu) + ' ký tự</span>' : '');

    var body = $('.ocr-page-body', node);
    if (!payload.markdown) {
      body.innerHTML = '<div class="ocr-waiting is-empty">' +
        (payload.nguon === 'loi'
          ? 'Mô hình không đọc được trang này — thử chạy lại, hoặc chọn “mọi trang”.'
          : 'Trang không có chữ nào.') + '</div>';
      return;
    }
    /* citations:false — chuỗi kiểu "[1]" trong văn bản gốc là nội dung của tài
       liệu, không phải marker trích dẫn của hệ thống. Để nguyên mặc định thì
       một điều khoản "khoản [2]" biến thành nút bấm dẫn đi đâu không ai biết. */
    body.innerHTML = MD.render(payload.markdown, { citations: false });
  }

  function ocrSyncProgress() {
    var bar = $('#ocrBar');
    if (!bar) return;
    var pct = ocr.tong ? Math.round((ocr.xong / ocr.tong) * 100) : 0;
    $('i', bar).style.width = pct + '%';
    $('#ocrProgressText').textContent = ocr.xong + '/' + ocr.tong + ' trang';
  }

  /** Chuyển giữa bản dựng và Markdown thô mà không gọi lại backend. */
  function ocrPaint() {
    var host = $('#ocrPages');
    if (!host || !ocr.tho) return;
    host.innerHTML = '';
    host.appendChild(el('pre', { class: 'ocr-raw' }, esc(ocrMarkdown())));
  }

  function ocrRebuildPages() {
    var host = $('#ocrPages');
    host.innerHTML = '';
    for (var i = 1; i <= ocr.tong; i++) host.appendChild(ocrPageNode(i));
    Object.keys(ocr.trang).forEach(function (so) { ocrFillPage(ocr.trang[so]); });
  }

  function ocrHeadNode(start) {
    var card = el('div', { class: 'card ocr-head' });
    card.appendChild(el('div', { class: 'card-head' },
      '<h3>' + esc(start.file_name) + '</h3>' +
      '<span class="pill">' + esc(start.model) + '</span>'));

    var stats = [
      ['Số trang', fmtNum(start.so_trang)],
      ['Mô hình đọc', fmtNum(start.so_trang_ocr) + ' trang'],
      ['Lấy thẳng lớp text', fmtNum(start.so_trang_digital) + ' trang'],
    ];
    card.appendChild(el('div', { class: 'ocr-stats' }, stats.map(function (s) {
      return '<div class="ocr-stat"><span>' + esc(s[0]) + '</span><b>' + esc(s[1]) + '</b></div>';
    }).join('')));

    card.appendChild(el('div', { class: 'ocr-progress', id: 'ocrBar' },
      '<i></i><span id="ocrProgressText">0/' + start.so_trang + ' trang</span>'));

    var tools = el('div', { class: 'ocr-tools' });
    var seg = el('div', { class: 'seg' });
    [['Văn bản có cấu trúc', false], ['Markdown thô', true]].forEach(function (mode) {
      var b = el('button', { class: 'seg-btn' + (ocr.tho === mode[1] ? ' is-on' : '') }, mode[0]);
      b.addEventListener('click', function () {
        if (ocr.tho === mode[1]) return;
        ocr.tho = mode[1];
        $$('.seg-btn', seg).forEach(function (x) { x.classList.remove('is-on'); });
        b.classList.add('is-on');
        if (ocr.tho) ocrPaint(); else { ocrRebuildPages(); ocrSyncProgress(); }
      });
      seg.appendChild(b);
    });
    tools.appendChild(seg);

    var chep = el('button', { class: 'ghost-btn sm' }, 'Sao chép Markdown');
    chep.addEventListener('click', function () {
      navigator.clipboard.writeText(ocrMarkdown()).then(
        function () { toast('Đã sao chép', 'ok', 1600); },
        function () { toast('Trình duyệt chặn clipboard', 'err'); });
    });
    tools.appendChild(chep);

    var tai = el('button', { class: 'ghost-btn sm' }, 'Tải .md');
    tai.addEventListener('click', function () {
      /* Dựng file ngay tại trình duyệt: chữ đã nằm sẵn trên trang, đi một vòng
         xuống backend để xin lại đúng thứ đó là thừa. */
      var blob = new Blob([ocrMarkdown()], { type: 'text/markdown;charset=utf-8' });
      var href = URL.createObjectURL(blob);
      var a = el('a', { href: href, download: start.file_name.replace(/\.[^.]+$/, '') + '.md' });
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(href); }, 1000);
    });
    tools.appendChild(tai);

    /* Lớp text có sẵn không đồng nghĩa với lớp text ĐÚNG: bản scan kèm OCR cũ
       vẫn thừa ký tự và thừa font nhúng nên qua hết ba tín hiệu phân loại, rồi
       trả về chữ sai. Không có ngưỡng nào tách được bản tốt với bản hỏng, nên
       thay vì đoán hộ thì để người đọc - người duy nhất nhìn ra chữ sai - bấm
       một nút. File đã nằm trên server, đọc lại không phải tải lên lần nữa. */
    if (ocr.cheDo === 'auto' && start.so_trang_digital > 0) {
      var lai = el('button', { class: 'ghost-btn sm' }, 'Chữ sai? Đọc lại bằng mô hình');
      lai.title = 'Bỏ lớp text có sẵn, ép cả ' + start.so_trang + ' trang qua mô hình';
      lai.addEventListener('click', function () {
        $('#ocrCheDo').value = 'tat_ca';
        ocrDoc(ocr.fileId, 'tat_ca');
      });
      tools.appendChild(lai);
    }

    card.appendChild(tools);
    return card;
  }

  function ocrSetBusy(busy) {
    var btn = $('#ocrRun');
    btn.disabled = busy || !ocr.file;
    btn.textContent = busy ? 'Đang đọc…' : 'Đọc tài liệu';
    $('#ocrCheDo').disabled = busy;
  }

  wireDropzone('#ocrDrop', '#ocrFile', function (file) {
    ocr.file = file;
    markDropzone('#ocrDrop', file);
    $('#ocrRun').disabled = false;
    $('#ocrResult').innerHTML = '<div class="result-empty">Đã chọn <b>' + esc(file.name) +
      '</b>. Bấm <b>Đọc tài liệu</b> để bắt đầu.</div>';
  });

  /** Tải file lên rồi đọc. Đọc lại đi thẳng vào `ocrDoc`, khỏi tải lên lần nữa. */
  async function ocrChay() {
    if (!ocr.file) { toast('Chọn file trước đã', 'err'); return; }
    var box = $('#ocrResult');
    ocrSetBusy(true);
    box.innerHTML = '';
    var loader = loaderNode('Đang tải “' + ocr.file.name + '” lên…');
    box.appendChild(loader);
    try {
      var up = await API.agentUpload(ocr.file, 'upload');
      ocr.fileId = up.file_id;
      loader.stop();
    } catch (e) {
      loader.stop();
      ocrSetBusy(false);
      box.innerHTML = '<div class="card" style="border-color:var(--err)">' +
        '<div class="card-head"><h3>Không tải được file lên</h3><span class="pill err">lỗi</span></div>' +
        '<div class="muted sm">' + esc(errText(e)) + '</div></div>';
      toast(errText(e), 'err');
      return;
    }
    ocrDoc(ocr.fileId, $('#ocrCheDo').value);
  }

  async function ocrDoc(fileId, cheDo) {
    if (!fileId) { toast('Chưa có file nào trên máy chủ', 'err'); return; }

    var box = $('#ocrResult');
    ocr.tong = 0;
    ocr.trang = {};
    ocr.xong = 0;
    ocr.meta = null;
    ocr.tho = false;
    ocr.cheDo = cheDo;
    ocr.ctrl = new AbortController();
    ocrSetBusy(true);

    box.innerHTML = '';
    var loader = loaderNode(
      cheDo === 'tat_ca' ? 'Đang ép cả tài liệu qua mô hình…' : 'Đang phân loại trang…',
      function () { ocr.ctrl.abort(); },
      { text: 'Trang scan đi qua mô hình thị giác, trang có sẵn chữ thì đọc thẳng.',
        slowAfter: 90 });
    box.appendChild(loader);

    try {
      var loi = null;

      await API.ocrStream({ file_id: fileId, che_do: cheDo }, {
        start: function (p) {
          loader.stop();
          box.innerHTML = '';
          ocr.tong = p.so_trang;
          box.appendChild(ocrHeadNode(p));
          box.appendChild(el('div', { class: 'ocr-pages', id: 'ocrPages' }));
          ocrRebuildPages();
          ocrSyncProgress();
        },
        trang: function (p) {
          ocr.trang[p.so_trang] = p;
          ocr.xong += 1;
          /* Đang xem Markdown thô thì phải dựng lại cả khối: nó là một chuỗi
             ghép từ mọi trang, không có ô riêng để điền vào như bản dựng. */
          if (ocr.tho) ocrPaint(); else ocrFillPage(p);
          ocrSyncProgress();
        },
        done: function (p) { ocr.meta = p; },
        error: function (p) { loi = p && p.detail; },
      }, ocr.ctrl.signal);

      loader.stop();
      if (loi) throw new API.ApiError(loi, 500, '');
      if (!ocr.meta) throw new API.ApiError('Kết nối đứt giữa chừng, tài liệu chưa đọc xong.', 0, '');

      var bar = $('#ocrBar');
      if (bar) {
        bar.classList.add('is-done');
        $('#ocrProgressText').textContent = ocr.meta.so_trang + ' trang · ' + ocr.meta.giay + 's' +
          (ocr.meta.so_trang_loi ? ' · ' + ocr.meta.so_trang_loi + ' trang đọc hỏng' : '');
      }
      toast('Đã đọc xong ' + ocr.meta.so_trang + ' trang', ocr.meta.so_trang_loi ? 'warn' : 'ok');
    } catch (e) {
      loader.stop();
      if (e && e.name === 'AbortError') {
        /* Đã dừng giữa chừng: giữ lại những trang đã đọc được thay vì xoá sạch -
           chúng vẫn là chữ thật của tài liệu, và đọc lại tốn đúng ngần ấy thời gian. */
        if (!ocr.tong) box.innerHTML = '<div class="result-empty">Đã dừng.</div>';
        else toast('Đã dừng — giữ lại ' + ocr.xong + ' trang đã đọc', '', 2600);
      } else {
        box.innerHTML = '<div class="card" style="border-color:var(--err)">' +
          '<div class="card-head"><h3>Không đọc được tài liệu</h3><span class="pill err">lỗi</span></div>' +
          '<div class="muted sm">' + esc(errText(e)) + '</div></div>';
        toast(errText(e), 'err');
      }
    } finally {
      ocr.ctrl = null;
      ocrSetBusy(false);
    }
  }

  $('#ocrRun').addEventListener('click', ocrChay);

  /* ─────────────────────────────── khởi động ─────────────────────────── */
  renderConvos();
  var initial = (location.hash || '').replace('#', '');
  showView(VIEWS[initial] ? initial : 'chat');
  chatInput.focus();

  window.addEventListener('hashchange', function () {
    var name = (location.hash || '').replace('#', '');
    if (VIEWS[name] && name !== currentView) showView(name);
  });
})();
