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
  /* Khối bố cục do backend sinh (app/documents/bo_cuc_hanh_chinh.py):
     "::: tieu-ngu" hai cột, "::: giua" căn giữa, "::: dau" con dấu, ":::" đóng.
     Chỉ backend sinh ra được, không phải LLM - nội dung bên trong vẫn escape
     như mọi chỗ khác nên không mở đường chèn HTML. */
  var KHOI_BO_CUC = { 'tieu-ngu': 'vb-tieu-ngu', 'giua': 'vb-giua', 'dau': 'vb-dau' };
  var NGAN_COT = '|||';

  function moKhoiBoCuc(line) {
    var m = line.match(/^\s*:::\s+([a-z-]+)\s*$/);
    return m && KHOI_BO_CUC[m[1]] ? KHOI_BO_CUC[m[1]] : null;
  }
  function dongKhoiBoCuc(line) { return /^\s*:::\s*$/.test(line); }

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

    /* `bat` = số của mục đầu tiên. Danh sách đánh số trong tài liệu thường bị
       nội dung khác chen vào giữa ("2. Địa chỉ..." cách "1. Tên công ty..." bởi
       một đoạn văn), nên mỗi mục thành một <ol> riêng - mà <ol> thì luôn đếm
       lại từ 1. Không giữ mốc thì văn bản ghi 1, 2, 3 hiện ra 1, 1, 1: người
       đọc thấy số khác hẳn số trên giấy. */
    function flushList(type, items, bat) {
      var mo = '<' + type + (type === 'ol' && bat > 1 ? ' start="' + bat + '"' : '') + '>';
      html.push(mo + items.map(function (it) {
        return '<li>' + inline(it, opts) + '</li>';
      }).join('') + '</' + type + '>');
    }

    while (i < lines.length) {
      var line = lines[i];

      // khối bố cục văn bản hành chính
      var lop = moKhoiBoCuc(line);
      if (lop) {
        var than = [];
        i++;
        while (i < lines.length && !dongKhoiBoCuc(lines[i])) { than.push(lines[i]); i++; }
        i++;
        if (lop === 'vb-tieu-ngu') {
          html.push('<div class="vb-tieu-ngu">' + than.map(function (r) {
            var o = r.split(NGAN_COT);
            return '<div class="vb-cot">' + inline((o[0] || '').trim(), opts) + '</div>' +
                   '<div class="vb-cot">' + inline((o[1] || '').trim(), opts) + '</div>';
          }).join('') + '</div>');
        } else if (lop === 'vb-giua') {
          /* Dòng đầu là tên văn bản, phần còn lại là phụ đề - tách bằng cấu
             trúc chứ không bằng nth-child, vì số dòng phụ đề thay đổi. */
          html.push('<div class="vb-giua">' +
            '<div class="vb-ten">' + inline((than[0] || '').trim(), opts) + '</div>' +
            (than.length > 1
              ? '<div class="vb-phu">' + than.slice(1).map(function (r) {
                  return inline(r.trim(), opts);
                }).join('<br />') + '</div>'
              : '') + '</div>');
        } else {
          html.push('<div class="' + lop + '">' + than.map(function (r) {
            return inline(r.trim(), opts);
          }).join('<br />') + '</div>');
        }
        continue;
      }

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
        flushList('ul', ul, 1);
        continue;
      }

      // danh sách có thứ tự
      if (/^\s*\d+[.)]\s+/.test(line)) {
        var ol = [];
        var bat = parseInt((line.match(/^\s*(\d+)/) || [])[1], 10) || 1;
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
          ol.push(lines[i].replace(/^\s*\d+[.)]\s+/, '')); i++;
        }
        flushList('ol', ol, bat);
        continue;
      }

      // đoạn văn: gom tới dòng trắng; xuống dòng đơn giữ bằng <br>
      var para = [];
      while (i < lines.length && lines[i].trim() &&
             !/^\s*(#{1,6}\s|>|```|:::|[-*+•–]\s|\d+[.)]\s)/.test(lines[i]) &&
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
