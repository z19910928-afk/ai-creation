(function (root) {
  'use strict';

  const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const entityList = value => Array.isArray(value) && value.every(item => object(item) && typeof item.id === 'string');

  function changedRange(before, after) {
    const a = Array.from(before);
    const b = Array.from(after);
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) start++;
    let suffix = 0;
    while (suffix < a.length - start && suffix < b.length - start &&
      a[a.length - suffix - 1] === b[b.length - suffix - 1]) suffix++;
    return {start, end: a.length - suffix, replacement: b.slice(start, b.length - suffix)};
  }

  function mergeText(base, local, remote) {
    if (equal(local, remote)) return {value: local};
    if (equal(local, base)) return {value: remote};
    if (equal(remote, base)) return {value: local};
    const left = changedRange(base, local);
    const right = changedRange(base, remote);
    // A shared start makes an insertion's placement against a deletion or replacement ambiguous.
    const sameStart = left.start === right.start;
    if (sameStart || !(left.end <= right.start || right.end <= left.start)) return {conflict: true};
    const chars = Array.from(base);
    for (const change of [left, right].sort((a, b) => b.start - a.start)) {
      chars.splice(change.start, change.end - change.start, ...change.replacement);
    }
    return {value: chars.join('')};
  }

  function merge(base, local, remote, choices = {}) {
    const conflicts = [];
    function visit(before, ours, theirs, path) {
      const key = path.join('/');
      if (Object.prototype.hasOwnProperty.call(choices, key)) return copy(choices[key]);
      if (path.length === 1 && ['version', 'head_revision', 'project_id', 'branch_id'].includes(path[0])) return copy(theirs);
      if (equal(ours, theirs)) return copy(ours);
      if (equal(ours, before)) return copy(theirs);
      if (equal(theirs, before)) return copy(ours);
      if (path.at(-1) === 'updated') {
        const left = typeof ours === 'number' ? ours : Date.parse(ours);
        const right = typeof theirs === 'number' ? theirs : Date.parse(theirs);
        if (Number.isFinite(left) && Number.isFinite(right)) return Math.max(left, right);
        return copy(theirs);
      }
      if (typeof before === 'string' && typeof ours === 'string' && typeof theirs === 'string' &&
        ['body', 'summary', 'description', 'recap', 'scene_plan', 'next_brief'].includes(path.at(-1))) {
        const result = mergeText(before, ours, theirs);
        if (!result.conflict) return result.value;
      }
      if (object(before) && object(ours) && object(theirs)) {
        const value = {};
        for (const field of new Set([...Object.keys(before), ...Object.keys(ours), ...Object.keys(theirs)])) {
          const result = visit(before[field], ours[field], theirs[field], [...path, field]);
          if (result !== undefined) value[field] = result;
        }
        return value;
      }
      if (entityList(before) && entityList(ours) && entityList(theirs)) {
        const bm = new Map(before.map(item => [item.id, item]));
        const lm = new Map(ours.map(item => [item.id, item]));
        const rm = new Map(theirs.map(item => [item.id, item]));
        const ids = [...theirs.map(item => item.id), ...ours.filter(item => !rm.has(item.id)).map(item => item.id)];
        return ids.map(id => visit(bm.get(id), lm.get(id), rm.get(id), [...path, id])).filter(item => item !== undefined);
      }
      conflicts.push({path: key, base: copy(before), local: copy(ours), remote: copy(theirs)});
      return copy(ours);
    }
    return {value: visit(base, local, remote, []), conflicts};
  }

  function codepointOffset(text, utf16Offset) {
    return Array.from(String(text).slice(0, utf16Offset)).length;
  }

  function utf16Offset(text, codepoint) {
    return Array.from(String(text)).slice(0, codepoint).join('').length;
  }

  const api = {copy, equal, merge, mergeText, changedRange, codepointOffset, utf16Offset};
  root.MoyuSync = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
