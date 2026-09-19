/* ===========================================================================
 * Bộ render Markdown tối giản, không phụ thuộc thư viện ngoài.
 *
 * Chỉ hỗ trợ đúng những gì backend sinh ra: heading, danh sách, bảng (quan
 * trọng nhất - báo cáo toàn bảng), khối code, trích dẫn, in đậm/nghiêng và
 * marker trích dẫn [1] -> chỗ bấm được.
 *
 * Mọi thứ đều escape trước khi ghép, nên nội dung do LLM sinh không chèn
 * được HTML vào trang.
 * ======================================================================== */
(function (global) {
  'use strict';

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // --- inline ------------------------------------------------------------
  function inline(text, opts) {
    var out = escapeHtml(text);

    // code `...` -> cất tạm để bold/italic không ăn vào bên trong
    var codes = [];
    out = out.replace(/`([^`\n]+)`/g, function (_, c) {
      codes.push(c);
      return '@@CODE' + (codes.length - 1) + '@@';
    });

    out = out.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    out = out.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
    out = out.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');

    // marker trích dẫn: [1] hoặc [1][2] hoặc [1, 2]
    if (!opts || opts.citations !== false) {
      out = out.replace(/\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g, function (_, group) {
        return group.split(/\s*,\s*/).map(function (n) {
          return '<sup class="cite" data-cite="' + n + '" role="button" tabindex="0" title="Xem nguồn ' + n + '">' + n + '</sup>';
        }).join('');
      });
    }

    out = out.replace(/@@CODE(\d+)@@/g, function (_, i) {
      return '<code>' + escapeHtml(codes[Number(i)]) + '</code>';
    });
    return out;
  }

  // --- block -------------------------------------------------------------
  function isTableRow(line) { return /^\s*\|.*\|\s*$/.test(line); }
  function isDivider(line) { return /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line) && line.indexOf('-') >= 0; }

  function splitRow(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|')
      .map(function (c) { return c.trim(); });
  }

  function render(src, opts) {
    var lines = String(src == null ? '' : src).replace(/\r\n?/g, '\n').split('\n');
    var html = [];
    var i = 0;

    function flushList(type, items) {
      html.push('<' + type + '>' + items.map(function (it) {
        return '<li>' + inline(it, opts) + '</li>';
      }).join('') + '</' + type + '>');
    }

    while (i < lines.length) {
      var line = lines[i];

      // khối code
      if (/^\s*```/.test(line)) {
        var buf = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++;
        html.push('<pre><code>' + escapeHtml(buf.join('\n')) + '</code></pre>');
        continue;
      }

      if (!line.trim()) { i++; continue; }

      // ngăn cách
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { html.push('<hr />'); i++; continue; }

      // heading
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        var lvl = Math.min(h[1].length + 1, 6);
        html.push('<h' + lvl + '>' + inline(h[2], opts) + '</h' + lvl + '>');
        i++; continue;
      }

      // bảng
      if (isTableRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
        var head = splitRow(line);
        i += 2;
        var rows = [];
        while (i < lines.length && isTableRow(lines[i])) { rows.push(splitRow(lines[i])); i++; }
        html.push('<div class="table-scroll"><table><thead><tr>' +
          head.map(function (c) { return '<th>' + inline(c, opts) + '</th>'; }).join('') +
          '</tr></thead><tbody>' +
          rows.map(function (r) {
            return '<tr>' + r.map(function (c) { return '<td>' + inline(c, opts) + '</td>'; }).join('') + '</tr>';
          }).join('') + '</tbody></table></div>');
        continue;
      }

      // trích dẫn
      if (/^\s*>\s?/.test(line)) {
        var quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, '')); i++;
        }
        html.push('<blockquote>' + render(quote.join('\n'), opts) + '</blockquote>');
        continue;
      }

      // danh sách không thứ tự (nhận cả gạch đầu dòng kiểu Việt)
      if (/^\s*[-*+•–]\s+/.test(line)) {
        var ul = [];
        while (i < lines.length && /^\s*[-*+•–]\s+/.test(lines[i])) {
          ul.push(lines[i].replace(/^\s*[-*+•–]\s+/, '')); i++;
        }
        flushList('ul', ul);
        continue;
      }

      // danh sách có thứ tự
      if (/^\s*\d+[.)]\s+/.test(line)) {
        var ol = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
          ol.push(lines[i].replace(/^\s*\d+[.)]\s+/, '')); i++;
        }
        flushList('ol', ol);
        continue;
      }

      // đoạn văn: gom tới dòng trắng; xuống dòng đơn giữ bằng <br>
      var para = [];
      while (i < lines.length && lines[i].trim() &&
             !/^\s*(#{1,6}\s|>|```|[-*+•–]\s|\d+[.)]\s)/.test(lines[i]) &&
             !(isTableRow(lines[i]) && i + 1 < lines.length && isDivider(lines[i + 1]))) {
        para.push(lines[i]); i++;
      }
      if (para.length) {
        html.push('<p>' + para.map(function (l) { return inline(l, opts); }).join('<br />') + '</p>');
      } else { i++; }
    }

    return html.join('\n');
  }

  global.MD = { render: render, inline: inline, escape: escapeHtml };
})(window);
