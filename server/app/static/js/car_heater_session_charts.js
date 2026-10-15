/* Dependency-free SVG time-series charts for session review. Missing values and
 * reporting gaps are drawn as breaks, never interpolated or replaced with zero. */
(() => {
  'use strict';
  const SVG = 'http://www.w3.org/2000/svg';
  const timeZone = 'Europe/Helsinki';
  const hhmm = ms => new Date(ms).toLocaleTimeString('en-GB', {hour12: false, timeZone, hour: '2-digit', minute: '2-digit'});
  const number = (value, unit) => value == null ? '—' : `${Number(value.toFixed(2))} ${unit}`;
  const node = (tag, attrs = {}, text) => {
    const el = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const html = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };

  function niceTicks(min, max, count) {
    const raw = (max - min) / count;
    const power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map(f => f * power).find(s => s >= raw) || raw;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step / 1e6; v += step) ticks.push(Number(v.toFixed(6)));
    return ticks;
  }

  // Split at explicit missing values and at gaps longer than the freshness limit.
  function segments(points, gapMs) {
    const result = [];
    let current = [];
    let previous = null;
    points.forEach(point => {
      if (point.y == null || (previous && point.x - previous.x > gapMs)) {
        if (current.length) result.push(current);
        current = [];
      }
      if (point.y != null) current.push(point);
      previous = point;
    });
    if (current.length) result.push(current);
    return result;
  }

  function thin(segment, limit) {
    if (segment.length <= limit) return segment;
    const stride = Math.ceil(segment.length / limit);
    return segment.filter((_, i) => i % stride === 0 || i === segment.length - 1);
  }

  function summaryText(series, unit, gapMs) {
    return series.map(s => {
      const values = s.points.filter(p => p.y != null);
      if (!values.length) return `${s.label}: no readings in this window (not available).`;
      const ys = values.map(p => p.y);
      const breaks = segments(s.points, gapMs).length - 1;
      const last = values[values.length - 1];
      const late = values.filter(p => p.late).length;
      return `${s.label}: ${values.length} readings, ${number(Math.min(...ys), unit)} to ${number(Math.max(...ys), unit)}, last ${number(last.y, unit)} at ${hhmm(last.x)}${breaks ? `; ${breaks} gap${breaks === 1 ? '' : 's'} in readings` : ''}${late ? `; ${late} late (hollow markers)` : ''}.`;
    });
  }

  function valueTable(series, unit) {
    // One row per local minute: the last reading of each series in that minute.
    const rows = new Map();
    series.forEach((s, index) => s.points.forEach(p => {
      const minute = Math.floor(p.x / 60000) * 60000;
      if (!rows.has(minute)) rows.set(minute, series.map(() => undefined));
      rows.get(minute)[index] = p.y;
    }));
    const details = html('details');
    details.append(html('summary', `Show values per minute (${rows.size} rows)`));
    const table = html('table');
    const head = html('tr');
    head.append(html('th', 'Time'), ...series.map(s => html('th', `${s.label} (${unit})`)));
    head.querySelectorAll('th').forEach(th => th.setAttribute('scope', 'col'));
    table.append(html('thead'));
    table.tHead.append(head);
    const body = html('tbody');
    [...rows.keys()].sort((a, b) => a - b).forEach(minute => {
      const row = html('tr');
      row.append(html('th', hhmm(minute)), ...rows.get(minute).map(v => html('td', v == null ? '—' : String(Number(v.toFixed(2))))));
      row.firstChild.setAttribute('scope', 'row');
      body.append(row);
    });
    table.append(body);
    details.append(table);
    return details;
  }

  function draw(container) {
    const spec = container._reviewChart;
    const title = container.dataset.title;
    const unit = container.dataset.unit;
    container.replaceChildren(html('h4', `${title} (${unit})`));
    const all = spec.series.flatMap(s => s.points.filter(p => p.y != null));
    if (!all.length) {
      container.append(html('p', spec.emptyText || `No ${title.toLowerCase()} measurements available for this session. Missing data is not shown as zero.`));
      return;
    }
    const legend = html('ul', undefined, 'review-legend');
    spec.series.forEach(s => {
      const item = html('li');
      const swatch = node('svg', {width: 28, height: 10, 'aria-hidden': 'true'});
      swatch.append(node('line', {x1: 0, y1: 5, x2: 28, y2: 5, stroke: s.color, 'stroke-width': 3, 'stroke-dasharray': s.dash || ''}));
      item.append(swatch, document.createTextNode(`${s.label} (${unit})${s.points.some(p => p.y != null) ? '' : ' · not available'}`));
      legend.append(item);
    });
    (spec.thresholds || []).forEach(t => legend.append(html('li', `${t.label} (dotted line)`)));
    const width = Math.max(280, Math.floor(container.clientWidth || 600));
    const height = width < 500 ? 200 : 240;
    const m = {left: 52, right: 12, top: 14, bottom: 28};
    const ys = all.map(p => p.y).concat((spec.thresholds || []).map(t => t.y));
    let [low, high] = [Math.min(...ys), Math.max(...ys)];
    if (spec.zero) low = Math.min(low, 0);
    if (low === high) { low -= 1; high += 1; }
    const pad = (high - low) * 0.06;
    low -= pad; high += pad;
    const x = ms => m.left + (ms - spec.start) / (spec.end - spec.start) * (width - m.left - m.right);
    const y = v => m.top + (high - v) / (high - low) * (height - m.top - m.bottom);
    const svg = node('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': `${title} chart. ${summaryText(spec.series, unit, spec.gapMs).join(' ')}`});
    niceTicks(low, high, 4).forEach(v => {
      svg.append(node('line', {x1: m.left, x2: width - m.right, y1: y(v), y2: y(v), class: 'review-grid-line'}));
      svg.append(node('text', {x: m.left - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'review-axis'}, String(Number(v.toFixed(2)))));
    });
    const stepMinutes = [5, 10, 15, 30, 60, 120, 180].find(s => (spec.end - spec.start) / (s * 60000) <= (width - m.left) / 70) || 360;
    const step = stepMinutes * 60000;
    for (let t = Math.ceil(spec.start / step) * step; t <= spec.end; t += step) {
      const anchor = x(t) > width - m.right - 20 ? 'end' : 'middle';
      svg.append(node('text', {x: anchor === 'end' ? width - m.right : x(t), y: height - 8, 'text-anchor': anchor, class: 'review-axis'}, hhmm(t)));
    }
    (spec.markers || []).filter(mk => mk.x >= spec.start && mk.x <= spec.end).forEach((mk, i) => {
      svg.append(node('line', {x1: x(mk.x), x2: x(mk.x), y1: m.top, y2: height - m.bottom, class: 'review-marker'}));
      // Labels near the right edge sit left of their line; rows avoid overlaps.
      const leftward = x(mk.x) + mk.label.length * 6.5 > width - m.right;
      svg.append(node('text', {x: x(mk.x) + (leftward ? -3 : 3), y: m.top + 10 + (i % 3) * 13, 'text-anchor': leftward ? 'end' : 'start', class: 'review-marker-label'}, mk.label));
    });
    (spec.thresholds || []).forEach(t => {
      svg.append(node('line', {x1: m.left, x2: width - m.right, y1: y(t.y), y2: y(t.y), class: 'review-threshold'}));
    });
    const limit = Math.max(60, width);
    spec.series.forEach(s => segments(s.points, spec.gapMs).forEach(segment => {
      const points = thin(segment, limit);
      if (points.length > 1) {
        svg.append(node('polyline', {
          points: points.map(p => `${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(' '),
          fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-dasharray': s.dash || '', 'stroke-linejoin': 'round',
        }));
      } else if (!points[0].late) {
        svg.append(node('circle', {cx: x(points[0].x), cy: y(points[0].y), r: 3, fill: s.color}));
      }
      // Late samples are real readings that arrived after the freshness limit.
      segment.filter(p => p.late).forEach(p => svg.append(node('circle', {cx: x(p.x), cy: y(p.y), r: 4, fill: 'none', stroke: s.color, 'stroke-width': 1.5})));
    }));
    const summary = html('ul', undefined, 'review-summary');
    summaryText(spec.series, unit, spec.gapMs).forEach(text => summary.append(html('li', text)));
    container.append(legend, svg, summary, valueTable(spec.series, unit));
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => document.querySelectorAll('.review-chart').forEach(c => c._reviewChart && draw(c)), 200);
  });

  window.CarHeaterSessionCharts = {
    render(container, spec) {
      if (!container) return;
      container._reviewChart = spec;
      draw(container);
    },
  };
})();
