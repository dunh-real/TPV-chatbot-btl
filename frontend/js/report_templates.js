/* Màn test mẫu báo cáo: phân tích một lần, sửa vùng mơ hồ rồi xác nhận chung. */
(function (global) {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var state = null, revision = 0;
  var status = $('draftTemplateStatus');
  function message(text, error) {
    status.textContent = text;
    status.style.color = error ? 'var(--err)' : 'var(--muted)';
  }
  function reset() {
    revision++; state = null;
    $('draftTemplateFields').replaceChildren();
    $('draftTemplateQuestions').replaceChildren();
    $('draftTemplateConfirm').hidden = true;
    message('');
  }
  function control(parent, title, tag, value) {
    var label = document.createElement('label'); label.className = 'field';
    var text = document.createElement('span'); text.textContent = title;
    var input = document.createElement(tag || 'input');
    if (input.tagName === 'INPUT') input.type = 'text';
    else input.rows = 2;
    input.value = value || '';
    label.append(text, input); parent.appendChild(label);
    return input;
  }
  function edited() {
    if (state) state.confirmed = false;
    message('Đã sửa vùng điền. Xác nhận lại mẫu trước khi soạn.');
  }
  function renderFields(data) {
    var questions = $('draftTemplateQuestions'); questions.replaceChildren();
    if (data.questions.length) {
      var details = document.createElement('details'); details.open = true;
      var summary = document.createElement('summary'); summary.textContent = 'Cần xem lại (' + data.questions.length + ')';
      var list = document.createElement('ul');
      data.questions.forEach(function (q) { var li = document.createElement('li'); li.textContent = q; list.appendChild(li); });
      details.append(summary, list); questions.appendChild(details);
    }
    var host = $('draftTemplateFields'); host.replaceChildren();
    state.rows = state.mapping.fields.map(function (field) {
      var card = document.createElement('details'); card.className = 'template-field';
      card.open = field.kind === 'input' || !field.definition;
      var heading = document.createElement('summary'); heading.textContent = field.label;
      card.appendChild(heading);
      var region = data.regions.find(function (r) { return r.target === field.target; });
      var original = document.createElement('p'); original.className = 'muted';
      original.textContent = 'Vùng gốc: ' + (region && (region.text || region.context) || 'Đoạn trống');
      card.appendChild(original);
      var row = { field: field };
      row.label = control(card, 'Tên vùng', 'input', field.label);
      row.definition = control(card, 'Ý nghĩa, kỳ, đơn vị cần điền', 'textarea', field.definition);
      if (field.text_template) row.textTemplate = control(card, 'Văn bản kỳ động ({{ky}}, {{ky_truoc}})', 'textarea', field.text_template);
      if (field.kind === 'input' && !field.text_template && !['ky','ky_truoc'].includes(field.input_key)) row.value = control(card, 'Thông tin bạn cung cấp', 'input', '');
      if (field.replace_existing) {
        var warning = document.createElement('p'); warning.className = 'pill warn';
        warning.textContent = 'Vùng này sẽ thay nội dung ví dụ trong mẫu.'; card.appendChild(warning);
      }
      var label = document.createElement('label');
      row.skip = document.createElement('input'); row.skip.type = 'checkbox';
      label.append(row.skip, document.createTextNode(' Giữ nguyên vùng này, không điền'));
      card.appendChild(label);
      card.addEventListener('input', function (event) { if (event.target !== row.value) edited(); });
      host.appendChild(card);
      return row;
    });
  }
  $('draftTemplateFile').addEventListener('change', reset);
  $('draftTemplateAnalyze').addEventListener('click', async function () {
    var file = $('draftTemplateFile').files[0];
    if (!file || !/\.docx$/i.test(file.name) || file.size > 10 * 1024 * 1024) {
      message('Chọn mẫu DOCX, tối đa 10 MB.', true); return;
    }
    reset(); var current = revision, button = this; button.disabled = true;
    message('Đang tải và phân tích mẫu…');
    try {
      var uploaded = await API.agentUpload(file, 'upload', false);
      var data = await API.templateAnalyze({ file_id: uploaded.file_id });
      if (current !== revision) return;
      if (!data.mapping) { message((data.questions || []).join(' ') || 'Chưa xác định được vùng điền.', true); return; }
      state = { file_id: uploaded.file_id, mapping: data.mapping, confirmed: false };
      renderFields(data);
      $('draftTemplateConfirm').hidden = false;
      message('Đã nhận diện ' + state.mapping.fields.length + ' vùng. Chỉ cần sửa chỗ chưa đúng rồi xác nhận chung.');
    } catch (e) { if (current === revision) message(e.message, true); }
    finally { button.disabled = false; }
  });
  $('draftTemplateConfirm').addEventListener('click', async function () {
    if (!state) return;
    var entry = state, button = this;
    entry.mapping = currentMapping();
    if (!entry.mapping.fields.length) { message('Cần ít nhất một vùng điền.', true); return; }
    button.disabled = true;
    var signature = JSON.stringify(entry.mapping);
    try {
      await API.templateConfirm({ file_id: entry.file_id, mapping: entry.mapping });
      // Sửa trong lúc đang gửi phải xác nhận lại; không dùng mapping cũ.
      if (state === entry && signature === JSON.stringify(currentMapping())) {
        entry.confirmed = true; message('Mẫu đã xác nhận. Chọn nguồn, nhập yêu cầu rồi bấm Soạn báo cáo.');
      }
    } catch (e) { if (state === entry) message(e.message, true); }
    finally { button.disabled = false; }
  });
  function currentMapping() {
    return Object.assign({}, state.mapping, { fields: state.rows.filter(function (r) { return !r.skip.checked; }).map(function (r) {
      return Object.assign({}, r.field, { label: r.label.value.trim(), definition: r.definition.value.trim(),
        text_template: r.textTemplate ? r.textTemplate.value : (r.field.text_template || '') });
    }) });
  }
  async function catalog() {
    var select = $('draftTemplateSelect'), label = $('draftTemplateCatalogStatus');
    select.replaceChildren(new Option('Chọn mẫu', '')); label.textContent = 'Đang lấy danh sách…';
    try {
      var rows = (await API.templates()).filter(function (r) { return r.uses_mapping; });
      rows.forEach(function (r) { select.add(new Option(r.ten_bao_cao + ' · v' + r.template_version, r.template_id + ':' + r.template_version)); });
      label.textContent = rows.length ? '' : 'Chưa có mẫu mapping đã công bố. Chọn tải mẫu DOCX để xem thử ngay.';
    } catch (e) { label.textContent = e.message; }
  }
  $('draftTemplateRefresh').addEventListener('click', catalog);
  $('draftTemplateSelect').addEventListener('change', async function () {
    var selected = this.value, parts = selected.split(':'), label = $('draftTemplateCatalogStatus');
    if (parts.length !== 2) { label.textContent = ''; return; }
    try {
      var version = await API.templateVersion(parts[0], parts[1]);
      if ($('draftTemplateSelect').value !== selected) return;
      label.textContent = (version.requires_period ? 'Cần nhập kỳ báo cáo. ' : '') +
        'Thông tin nhập riêng: ' + (version.required_inputs.map(function (f) { return (f.input_key || f.id) + ' (' + f.label + ')'; }).join(', ') || 'không có') + '. Nhập tại phần inputs bên dưới.';
    } catch (e) { label.textContent = e.message; }
  });
  $('draftTemplateMode').addEventListener('change', function () {
    $('draftTemplateUpload').hidden = this.value !== 'upload';
    $('draftTemplatePublished').hidden = this.value !== 'published';
    if (this.value === 'published') catalog();
  });
  $('draftSource').addEventListener('change', function () {
    $('draftFileField').hidden = this.value !== 'tai_lieu';
    $('draftDocIdsField').hidden = this.value !== 'kho_tri_thuc';
  });
  global.addEventListener('tpv-identity-change', function () {
    reset(); $('draftTemplateFile').value = '';
    $('draftTemplateSelect').replaceChildren(new Option('Chọn mẫu', ''));
  });
  global.ReportTemplates = {
    run: function (body, signal) {
      body.period = $('draftPeriod').value.trim();
      body.content = $('draftContent').value.trim();
      body.doc_ids = $('draftDocIds').value.split(/[\s,;]+/).filter(Boolean);
      var mode = $('draftTemplateMode').value;
      if (mode === 'upload') {
        if (!state || !state.confirmed) return Promise.reject(new Error('Phân tích và xác nhận mẫu trước khi soạn.'));
        var inputs = Object.assign({}, body.inputs);
        state.rows.filter(function (r) { return !r.skip.checked && r.value; }).forEach(function (r) {
          inputs[r.field.input_key || r.field.id] = r.value.value;
        });
        return API.templatePreview({ request: body.request, file_id: state.file_id, mapping: state.mapping,
          nguon: body.nguon, source_file_id: body.file_id || '', doc_ids: body.doc_ids, inputs: inputs,
          period: body.period, content: body.content }, signal);
      }
      if (mode === 'published') {
        var selected = $('draftTemplateSelect').value.split(':');
        if (selected.length !== 2) return Promise.reject(new Error('Chọn mẫu ERP đã công bố.'));
        body.template_id = Number(selected[0]); body.template_version = Number(selected[1]);
      }
      return API.draft(body, signal);
    },
  };
})(window);
