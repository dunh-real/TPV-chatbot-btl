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
      (doc.title ? '<div class="card-sub" style="margin-top:10px">Tiêu đề</div><div class="sm">' + esc(doc.title) + '</div>' : '') +
      (doc.outline && doc.outline.length
        ? '<div class="card-sub" style="margin-top:10px">Dàn ý dò được (' + doc.outline.length + ' mục)</div>' +
          '<div class="outline">' + doc.outline.map(function (h) {
            return '<div class="outline-item" style="padding-left:' + ((h.level - 1) * 14) + 'px" title="' + esc(h.text) + '">' +
              '<b>' + esc(h.block_id) + '</b>' + esc(h.text) + '</div>';
          }).join('') + '</div>'
        : '<div class="muted sm" style="margin-top:10px">Không dò được mục nào - tài liệu không chia mục hoặc không đánh số.</div>');
    box.appendChild(head);

    // rule check
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

    // llm review
    var review = data.llm_review || {};
    var keys = Object.keys(review).filter(function (k) { return (review[k] || []).length; });
    var lrCard = el('div', { class: 'card' });
    lrCard.innerHTML = '<div class="card-head"><h3>Chữ nghĩa (LLM, mọi lỗi phải trích nguyên văn)</h3>' +
      '<span class="pill">' + keys.length + ' khối</span></div>';
    if (!keys.length) {
      lrCard.innerHTML += '<div class="muted sm">Không phát hiện lỗi chữ nghĩa.</div>';
    } else {
      keys.forEach(function (k) {
        var items = review[k];
        lrCard.appendChild(block('Khối ' + k, items.length, items.map(function (f) {
          return '<div class="finding"><span class="finding-badge pill ' + (SEVERITY[f.severity] || 'warn') + '">' + esc(f.type) + '</span>' +
            '<div class="finding-body"><div class="finding-msg">' + esc(f.message || '') + '</div>' +
            '<div class="quote">' + esc(f.quote) + '</div>' +
            (f.suggest ? '<div class="suggest">→ ' + esc(f.suggest) + '</div>' : '') + '</div></div>';
        }).join(''), true));
      });
    }
    box.appendChild(lrCard);

    // nội dung + phân công
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
      taskCard.innerHTML = '<div class="card-head"><h3>Phân rã nhiệm vụ</h3><span class="pill">' + data.tasks.length + '</span></div>';
      data.tasks.forEach(function (t, i) {
        var row = el('div', { class: 'task-row', dataset: { mid: 'task-' + i } });
        sourceRegistry.set('task-' + i, t.refs || []);
        row.innerHTML = '<div class="task-head"><span class="task-dept">' + esc(t.department_name || t.department || 'Ngoài danh mục') + '</span>' +
          (t.department ? '<span class="pill">' + esc(t.department) + '</span>' : '') +
          (t.in_catalog ? '' : '<span class="pill warn">không có trong danh mục</span>') +
          (t.deadline ? '<span class="pill info">hạn ' + esc(t.deadline) + '</span>' : '') + '</div>' +
          '<div class="prose" style="font-size:13.4px">' + MD.render(t.task) + '</div>' +
          ((t.data_needed || []).length ? '<div class="tag-row" style="margin-top:6px">' +
            t.data_needed.map(function (d) { return '<span class="pill">' + esc(d) + '</span>'; }).join('') + '</div>' : '');
        var rb = sourcesBlock('Căn cứ trong văn bản', t.refs);
        if (rb) { rb.style.marginTop = '8px'; row.appendChild(rb); }
        taskCard.appendChild(row);
      });
      box.appendChild(taskCard);
    }

    box.appendChild(el('div', { class: 'card' })).appendChild(
      block('JSON đầy đủ', null, '<pre class="json">' + prettyJSON(data) + '</pre>', false));
  }

  $('#reviewRun').addEventListener('click', function () {
    if (!reviewFile) { toast('Chọn file trước đã', 'err'); return; }
    runWorkflow({
      button: this,
      box: $('#reviewResult'),
      label: 'Đang parse file, dựng dàn ý, chạy rule engine và soát chữ nghĩa theo lô…',
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
