/* UC37: danh sách tác vụ, kết quả, chạy lại, xóa và thông báo theo tài khoản. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (value) { return MD.escape(String(value == null ? '' : value)); };
  var labels = {Queued: 'Đang chờ', Processing: 'Đang xử lý', Completed: 'Hoàn thành', Failed: 'Lỗi', Cancelled: 'Đã dừng',
    Review: 'Soát tài liệu', Summarize: 'Tóm tắt', TaskExtract: 'Trích nhiệm vụ', Draft: 'Soạn báo cáo', Aggregate: 'Tổng hợp', Slide: 'Tạo slide', File: 'Xuất file'};
  var page = 1, total = 0, selected = null, account = '', polling = false, seen = new Set(), firstPoll = true;
  function identity() { return API.getTenant() + ':' + API.getUser(); }
  function syncIdentity() {
    if (account !== identity()) {
      account = identity(); selected = null; page = 1; seen.clear(); firstPoll = true;
      $('uc37Detail').textContent = 'Chọn tác vụ để xem kết quả.';
      $('uc37List').textContent = ''; $('uc37Notices').textContent = '';
      $('uc37Badge').textContent = 'UC37';
    }
    return account;
  }
  function message(text) { $('uc37Message').textContent = text || ''; }
  function stamp(value) { return value ? new Date(/Z$|[+]\d\d:\d\d$/.test(value) ? value : value + 'Z').toLocaleString('vi-VN') : '—'; }
  function toast(text, taskId) {
    var node = document.createElement('div'); node.className = 'toast'; node.textContent = text;
    var link = document.createElement('a'); link.href = '#tasks/' + taskId; link.textContent = ' Mở kết quả'; node.appendChild(link);
    $('toasts').appendChild(node); setTimeout(function () { node.remove(); }, 7000);
  }
  async function call(path, method, body) {
    var options = { method: method || 'GET' };
    if (body !== undefined) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(body); }
    return API.taskRequest(path, options);
  }
  async function download(path) {
    try { await API.taskDownload(path); } catch (e) { message(e.message); }
  }
  async function load() {
    var owner = syncIdentity();
    try {
      var q = new URLSearchParams({page: page, page_size: 20});
      if ($('uc37Status').value) q.set('status', $('uc37Status').value);
      if ($('uc37Kind').value) q.set('kind', $('uc37Kind').value);
      var data = await call('?' + q);
      if (owner !== identity()) return;
      total = data.total;
      $('uc37List').innerHTML = data.items.length ? '<table><thead><tr><th>Tác vụ</th><th>Trạng thái</th><th>Thời gian</th></tr></thead><tbody>' +
        data.items.map(function (t) { return '<tr><td><button class="uc37-open" data-id="' + t.id + '">#' + t.id + ' · ' + esc(labels[t.kind] || t.kind) + '</button><small>' + esc(t.agent) + '</small></td><td>' + esc(labels[t.status]) + '<small>' + t.progress + '%</small></td><td>' + esc(stamp(t.created_at)) + '</td></tr>'; }).join('') + '</tbody></table>' : '<p>Chưa có tác vụ phù hợp.</p>';
      $('uc37Page').textContent = 'Trang ' + page + ' · ' + total + ' tác vụ';
      $('uc37Prev').disabled = page <= 1; $('uc37Next').disabled = page * 20 >= total;
      $('uc37List').querySelectorAll('[data-id]').forEach(function (b) { b.onclick = function () { open(Number(b.dataset.id)); }; });
      message('');
    } catch (e) { if (owner === identity()) message(e.message); }
  }
  async function open(id) {
    selected = id; location.hash = 'tasks/' + id;
    await detail(id);
  }
  async function detail(id) {
    var owner = syncIdentity(); selected = id;
    try {
      var t = await call('/' + id);
      if (owner !== identity() || selected !== id) return;
      var result = t.result || {};
      $('uc37Detail').innerHTML = '<h3>#' + id + ' · ' + esc(labels[t.kind] || t.kind) + '</h3>' +
        '<p>' + esc(labels[t.status]) + ' · ' + t.progress + '% · Agent: ' + esc(t.agent) + '</p>' +
        '<p>Bắt đầu: ' + esc(stamp(t.started_at)) + '<br>Kết thúc: ' + esc(stamp(t.ended_at)) + '</p>' +
        (t.retry_of_task_id ? '<p>Chạy lại từ tác vụ #' + t.retry_of_task_id + '</p>' : '') +
        (t.error ? '<p class="uc37-error">' + esc(t.error) + '</p>' : '') +
        (t.template ? '<p>Mẫu: ' + esc(t.template.name) + ' (' + esc(t.template.code) + ') · phiên bản ' + esc(t.template.version) + '</p>' : '') +
        '<div class="uc37-toolbar">' + (t.download_url ? '<button class="btn" id="uc37Download">Tải kết quả</button>' : '') +
        (t.can_retry ? '<button class="btn" id="uc37Retry">Chạy lại tác vụ lỗi</button>' : '') +
        (t.can_delete ? '<button class="btn" id="uc37Delete">Xóa đầu ra</button>' : '') + '</div>' +
        '<label><input type="checkbox" id="uc37TaskNotify" ' + (t.notify_on_finish ? 'checked' : '') + '> Thông báo cho tác vụ này</label>' +
        (t.output_deleted ? '<p>Đầu ra đã được xóa.</p>' : '') +
        (result.summary ? '<div class="prose">' + MD.render(result.summary) + '</div>' : '') +
        '<h4>Phiên bản đầu ra</h4><div id="uc37Versions"></div><h4>Tài liệu nguồn</h4><div id="uc37Sources"></div>' +
        '<details><summary>Nội dung kết quả</summary><pre>' + esc(JSON.stringify(t.result, null, 2)) + '</pre></details>';
      if (t.template && t.template.download_url) {
        var templateButton = document.createElement('button'); templateButton.className = 'btn'; templateButton.textContent = 'Tải mẫu đã sử dụng';
        templateButton.onclick = function () { download(t.template.download_url); }; $('uc37Detail').appendChild(templateButton);
      }
      if ($('uc37Download')) $('uc37Download').onclick = function () { download(t.download_url); };
      $('uc37TaskNotify').onchange = async function () { try { await call('/' + id + '/notify', 'PATCH', { enabled: this.checked }); } catch (e) { message(e.message); await detail(id); } };
      if ($('uc37Retry')) $('uc37Retry').onclick = async function () {
        this.disabled = true;
        try { var next = await call('/' + id + '/retry', 'POST'); await load(); await open(next.task_id); }
        catch (e) { message(e.message); this.disabled = false; }
      };
      if ($('uc37Delete')) $('uc37Delete').onclick = async function () {
        if (!confirm('Xóa sản phẩm đầu ra? Lịch sử tác vụ vẫn được giữ.')) return;
        try { await call('/' + id + '/output', 'DELETE'); await detail(id); await load(); } catch (e) { message(e.message); }
      };
      var versions = $('uc37Versions');
      (t.versions || []).forEach(function (v) {
        var b = document.createElement('button'); b.className = 'btn'; b.textContent = 'Phiên bản ' + v.version;
        b.onclick = function () { download(v.download_url); }; versions.appendChild(b);
      });
      if (!t.versions.length) versions.textContent = 'Chưa có phiên bản được lưu.';
      var sources = $('uc37Sources');
      (t.sources || []).forEach(function (s) {
        var line = document.createElement('p'); line.textContent = s.title || (s.document_id ? 'Tài liệu #' + s.document_id : 'File nguồn đã tải lên');
        if (s.page) line.textContent += ' · trang ' + s.page;
        if (s.available) {
          var b = document.createElement('button'); b.className = 'btn'; b.textContent = 'Tải bản nguồn';
          b.onclick = function () { download(s.download_url || '/api/documents/' + encodeURIComponent('sql:' + s.document_id) + '/original'); };
          line.appendChild(b);
        } else line.textContent += ' · không còn khả dụng';
        sources.appendChild(line);
      });
      if (!t.sources.length) sources.textContent = 'Không có tài liệu nguồn được liên kết.';
    } catch (e) { if (owner === identity()) message(e.message); }
  }
  async function poll() {
    if (polling || document.hidden) return;
    polling = true;
    var owner = syncIdentity();
    try {
      var data = await call('/notifications');
      if (owner !== identity()) return;
      var unread = data.items.filter(function (n) { return !n.is_read; });
      $('uc37Badge').textContent = unread.length ? String(unread.length) : 'UC37';
      var box = $('uc37Notices'); box.textContent = '';
      unread.forEach(function (n) {
        if (!firstPoll && !seen.has(n.task_id)) toast('Tác vụ #' + n.task_id + ': ' + (labels[n.status] || n.status), n.task_id);
        seen.add(n.task_id);
        var b = document.createElement('button'); b.className = 'btn'; b.textContent = '#' + n.task_id + ' · ' + labels[n.kind] + ' · ' + labels[n.status];
        b.onclick = async function () { try { await call('/notifications/' + n.task_id + '/read', 'PATCH'); await open(n.task_id); await poll(); } catch (e) { message(e.message); } };
        box.appendChild(b);
      });
      firstPoll = false;
      if (location.hash.indexOf('#tasks') === 0) { await load(); if (selected) await detail(selected); }
    } catch (_) { /* Danh tính chưa chọn hoặc chưa được cấp quyền: không lặp toast lỗi. */ }
    finally { polling = false; }
  }
  $('uc37Notify').checked = localStorage.getItem('tpv.notifyTasks') === 'true';
  $('uc37Notify').onchange = function () { localStorage.setItem('tpv.notifyTasks', String(this.checked)); };
  $('uc37Refresh').onclick = load;
  ['uc37Status', 'uc37Kind'].forEach(function (id) { $(id).onchange = function () { page = 1; load(); }; });
  $('uc37Prev').onclick = function () { if (page > 1) { page--; load(); } };
  $('uc37Next').onclick = function () { if (page * 20 < total) { page++; load(); } };
  $('uc37Form').onsubmit = async function (event) {
    event.preventDefault(); $('uc37Submit').disabled = true; message('Đang gửi tác vụ…');
    try {
      var kind = $('uc37CreateKind').value, params = {}, file = $('uc37File').files[0];
      if (['Review', 'Summarize', 'TaskExtract', 'Draft'].indexOf(kind) >= 0) {
        if (!file) throw new Error('Chọn file nguồn trước khi gửi.');
        params.file_id = (await API.agentUpload(file)).file_id;
      }
      if (['Draft', 'Aggregate', 'Slide'].indexOf(kind) >= 0) {
        if (!$('uc37Request').value.trim()) throw new Error('Nhập yêu cầu cần thực hiện.');
        params.request = $('uc37Request').value.trim(); params.inputs = {};
        if ($('uc37Number').value.trim()) params.inputs.so_ky_hieu = $('uc37Number').value.trim();
        if (kind === 'Draft') params.nguon = 'tai_lieu';
      }
      var task = await call('', 'POST', { kind: kind, parameters: params, notify_on_finish: $('uc37Notify').checked });
      await load(); await open(task.task_id);
    } catch (e) { message(e.message); }
    finally { $('uc37Submit').disabled = false; }
  };
  window.TaskUI = { show: async function () { await load(); var id = Number(location.hash.split('/')[1]); if (Number.isInteger(id) && id > 0) await detail(id); } };
  window.addEventListener("tpv-identity-change", function () { syncIdentity(); poll(); });
  setInterval(poll, 5000); poll();
})();
