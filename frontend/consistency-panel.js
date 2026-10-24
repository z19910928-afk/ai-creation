'use strict';
/* 墨屿一致性检查面板（原生脚本，无构建，无框架）。
 *
 * 依赖 app.js 暴露的全局：views / state / esc / toast / render / activeChapter。
 * 面板只读：只调用 GET /api/consistency，不写数据库、不改任何稿件。
 *
 * 渲染约定：views.consistency 先同步返回骨架，MutationObserver 监听 #main
 * 出现 [data-consistency-root] 后再异步取数并重绘；筛选只影响清单，分数与
 * 分类统计始终来自后端全量结果。
 */
(function () {
  const CATEGORY_ORDER = ['fact', 'timeline', 'knowledge', 'attribute', 'relation', 'foreshadow', 'evidence'];
  const CATEGORY_LABELS = {
    fact: '事实矛盾',
    timeline: '时间线矛盾',
    knowledge: '知识越界',
    attribute: '属性漂移',
    relation: '关系/称谓矛盾',
    foreshadow: '伏笔断线',
    evidence: '来源失效'
  };
  const SEVERITIES = [
    ['blocker', '阻断'],
    ['high', '高危'],
    ['medium', '中危'],
    ['low', '低危']
  ];
  const FILTERS = [
    { key: 'all', label: '全部', param: null, title: '显示全部问题' },
    { key: 'blocker', label: '阻断', param: 'blocker', title: '显示阻断及以上问题' },
    { key: 'high', label: '高危', param: 'high', title: '显示高危及以上问题（含阻断）' },
    { key: 'medium', label: '中危', param: 'medium', title: '显示中危及以上问题（含阻断、高危）' },
    { key: 'low', label: '低危', param: 'low', title: '显示低危及以上问题（全部等级）' }
  ];
  const EVIDENCE_LABELS = {
    entity_id: '实体',
    assertion_id: '断言',
    assertion_ids: '冲突断言',
    memory_id: '记忆',
    source_span_id: '原句片段',
    chapter_id: '章节',
    chapter_ids: '章节',
    event_id: '事件',
    event_ids: '事件',
    ordinal: '序位',
    times: '时间标记',
    objects: '冲突取值',
    known_by: '知情角色',
    attribute: '属性',
    character_state_ids: '状态记录',
    relation_id: '关系',
    relation_ids: '关系',
    relation_type: '关系类型',
    missing_entity_id: '缺失实体',
    status: '状态',
    plot_id: '伏笔',
    payoff_ids: '回收链接',
    plant_ids: '埋设链接',
    span_revision: '原句版本',
    latest_revision: '最新版本'
  };
  const OFFLINE_TEXT = '无法连接本机服务，一致性检查暂时不可用。请先启动墨屿本地服务，再点击「重新检查」；离线时仍可继续写作，本面板不会改动任何稿件。';

  let selectedFilter = 'all';
  let currentFilter = 'all';
  let currentReport = null;
  let loading = false;
  let errorText = '';
  let requestSequence = 0;
  let mountedRoot = null;
  let observer = null;

  function filterMeta(key) {
    return FILTERS.find(item => item.key === key) || FILTERS[0];
  }

  function severityLabel(key) {
    const found = SEVERITIES.find(item => item[0] === key);
    return found ? found[1] : String(key || '未知');
  }

  function numberOrZero(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    try {
      return date.toLocaleString('zh-CN', { hour12: false });
    } catch (error) {
      return String(value);
    }
  }

  function gradeOf(score) {
    if (score >= 90) return 'A';
    if (score >= 75) return 'B';
    if (score >= 60) return 'C';
    return 'D';
  }

  function safeChapterId(value) {
    const text = String(value == null ? '' : value);
    return /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(text) ? text : '';
  }

  function chapterIndex() {
    const chapters = Array.isArray(state && state.chapters) ? state.chapters : [];
    const byId = new Map();
    const byOrdinal = new Map();
    chapters.forEach((chapter, index) => {
      if (!chapter || chapter.id == null) return;
      const id = String(chapter.id);
      const ordinal = numberOrZero(chapter.ordinal) || index + 1;
      const entry = { id, ordinal, title: String(chapter.title == null ? '未命名章节' : chapter.title) };
      byId.set(id, entry);
      if (!byOrdinal.has(ordinal)) byOrdinal.set(ordinal, entry);
    });
    return { byId, byOrdinal };
  }

  function chapterButton(chapter) {
    const id = safeChapterId(chapter && chapter.id);
    if (!id) return '';
    return '<button type="button" class="consistency-chapter-link" data-chapter="' + esc(id) +
      '" title="跳到该章节">第' + esc(chapter.ordinal) + '章《' + esc(chapter.title) + '》</button>';
  }

  /* 把正文里的「第N章《标题》」替换成可跳转按钮；先转义再按纯字符串替换，避免 XSS。 */
  function linkChapterRefs(rawText) {
    let html = esc(rawText == null ? '' : rawText);
    const index = chapterIndex();
    if (!index.byOrdinal.size) return html;
    index.byOrdinal.forEach(chapter => {
      const needle = '第' + chapter.ordinal + '章《' + esc(chapter.title) + '》';
      const replacement = chapterButton(chapter);
      if (needle && replacement && html.indexOf(needle) !== -1) {
        html = html.split(needle).join(replacement);
      }
    });
    return html;
  }

  function severityBadge(key) {
    const safe = SEVERITIES.some(item => item[0] === key) ? key : 'unknown';
    return '<span class="consistency-badge consistency-badge--' + esc(safe) + '">' +
      esc(severityLabel(safe)) + '</span>';
  }

  function evidenceScalar(value, index, isChapter) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') {
      try {
        return '<code>' + esc(JSON.stringify(value)) + '</code>';
      } catch (error) {
        return esc(String(value));
      }
    }
    const text = String(value);
    if (isChapter) {
      const chapter = index.byId.get(text);
      if (chapter) return chapterButton(chapter);
      return esc(text) + '（已不存在）';
    }
    return esc(text);
  }

  function evidenceValue(value, index, isChapter) {
    if (Array.isArray(value)) {
      if (!value.length) return '（空）';
      return value.map(item => evidenceScalar(item, index, isChapter)).join('、');
    }
    return evidenceScalar(value, index, isChapter);
  }

  function evidenceHtml(evidence) {
    if (!evidence || typeof evidence !== 'object' || Array.isArray(evidence)) return '';
    const entries = Object.entries(evidence);
    if (!entries.length) return '';
    const index = chapterIndex();
    const rows = entries.map(([key, value]) => {
      const isChapter = key === 'chapter_id' || key === 'chapter_ids';
      return '<span class="consistency-evidence-item"><span class="consistency-evidence-key">' +
        esc(EVIDENCE_LABELS[key] || key) + '</span><span class="consistency-evidence-value">' +
        evidenceValue(value, index, isChapter) + '</span></span>';
    });
    return '<div class="consistency-evidence" aria-label="证据定位">' + rows.join('') + '</div>';
  }

  function findingHtml(item) {
    const severity = item && SEVERITIES.some(entry => entry[0] === item.severity) ? item.severity : 'unknown';
    const category = item && (item.category_label || CATEGORY_LABELS[item.category] || item.category || '未分类');
    const suggestion = item && item.suggestion
      ? '<p class="consistency-finding-suggestion"><span>建议</span>' + linkChapterRefs(item.suggestion) + '</p>'
      : '';
    return '<article class="consistency-finding" data-severity="' + esc(severity) + '">' +
      '<div class="consistency-finding-head">' + severityBadge(severity) +
      '<span class="consistency-finding-category">' + esc(category) + '</span>' +
      '<h4>' + esc(item && item.title ? item.title : '未命名问题') + '</h4></div>' +
      '<p class="consistency-finding-detail">' + linkChapterRefs(item && item.detail ? item.detail : '') + '</p>' +
      suggestion +
      evidenceHtml(item && item.evidence) +
      '<p class="consistency-finding-id">问题编号 ' + esc(item && item.id ? item.id : '') + '</p>' +
      '</article>';
  }

  function totalFindings(report) {
    if (!report || !report.counts) return 0;
    return SEVERITIES.reduce((sum, entry) => sum + numberOrZero(report.counts[entry[0]]), 0);
  }

  function scoreCardHtml() {
    if (!currentReport) {
      const pendingText = errorText ? '本次检查未完成' : '正在检查全书一致性…';
      return '<section class="consistency-score-card consistency-score-card--loading" aria-label="一致性分数">' +
        '<div class="consistency-score-main"><div class="consistency-score-number">' +
        '<span class="consistency-score-value">—</span><small>分</small></div>' +
        '<span class="consistency-grade consistency-grade--pending">等级 …</span></div>' +
        '<p class="consistency-summary" role="status">' + esc(pendingText) + '</p>' +
        '<p class="consistency-meta">检查覆盖事实、时间线、知识边界、属性、关系、伏笔与来源七类规则。</p>' +
        '</section>';
    }
    const report = currentReport;
    const grade = String(report.grade || gradeOf(report.score));
    const counts = SEVERITIES.map(entry =>
      '<li>' + severityBadge(entry[0]) + '<strong>' + numberOrZero(report.counts && report.counts[entry[0]]) +
      '</strong></li>').join('');
    const metaParts = [
      '检查 ' + numberOrZero(report.totalExamined) + ' 条记录',
      '引擎 v' + esc(report.version == null ? '' : report.version),
      report.generatedAt ? '生成于 ' + esc(formatTime(report.generatedAt)) : ''
    ].filter(Boolean);
    return '<section class="consistency-score-card" aria-label="一致性分数">' +
      '<div class="consistency-score-main">' +
      '<div class="consistency-score-number"><span class="consistency-score-value">' + esc(report.score) +
      '</span><small>分</small></div>' +
      '<span class="consistency-grade consistency-grade--' + esc(grade) + '" title="一致性等级">' +
      '<span>等级</span><strong>' + esc(grade) + '</strong></span>' +
      '</div>' +
      '<p class="consistency-summary">' + esc(report.summary || '') + '</p>' +
      '<ul class="consistency-counts" aria-label="按严重度统计">' + counts + '</ul>' +
      '<p class="consistency-meta">' + metaParts.join(' · ') + '</p>' +
      '</section>';
  }

  function categoryBreakdown(category) {
    const parts = SEVERITIES.map(entry => {
      const count = numberOrZero(category && category[entry[0]]);
      return count > 0 ? severityLabel(entry[0]) + ' ' + count : '';
    }).filter(Boolean);
    return parts.length ? parts.join(' · ') : '无问题';
  }

  function categoriesHtml() {
    const categories = (currentReport && currentReport.categories) || {};
    const examined = (currentReport && currentReport.examined) || {};
    const keys = CATEGORY_ORDER.slice();
    Object.keys(categories).forEach(key => {
      if (keys.indexOf(key) === -1) keys.push(key);
    });
    const cards = keys.map(key => {
      const category = categories[key] || {};
      const count = numberOrZero(category.count);
      const label = category.label || CATEGORY_LABELS[key] || key;
      const examinedCount = numberOrZero(examined[key]);
      const note = currentReport
        ? esc(categoryBreakdown(category)) + (examinedCount ? ' · 核对 ' + esc(examinedCount) + ' 条' : '')
        : '等待检查结果';
      return '<article class="consistency-category' + (count > 0 ? ' has-issues' : '') + '">' +
        '<span class="consistency-category-label">' + esc(label) + '</span>' +
        '<strong>' + (currentReport ? esc(count) : '—') + '</strong>' +
        '<small>' + note + '</small>' +
        '</article>';
    }).join('');
    return '<section class="consistency-categories" aria-label="七类一致性统计">' + cards + '</section>';
  }

  function filtersHtml() {
    return FILTERS.map(item =>
      '<button type="button" class="consistency-filter" data-consistency-filter="' + esc(item.key) +
      '" aria-pressed="' + String(selectedFilter === item.key) + '" title="' + esc(item.title) + '">' +
      esc(item.label) + '</button>').join('');
  }

  function findingsListHtml() {
    if (!currentReport) {
      if (errorText) {
        return '<div class="consistency-state consistency-state--error" role="alert">' +
          '<strong>暂时读不到一致性报告</strong><p>' + esc(errorText) + '</p>' +
          '<button type="button" class="ghost-btn" data-consistency-retry>重新检查</button></div>';
      }
      return '<div class="consistency-loading" role="status"><span class="consistency-spinner" aria-hidden="true"></span>' +
        '正在核对全书事实、时间线、知识边界与伏笔…</div>';
    }
    if (errorText) {
      return '<div class="consistency-state consistency-state--error" role="alert">' +
        '<strong>这次筛选没有完成</strong><p>' + esc(errorText) + '</p>' +
        '<button type="button" class="ghost-btn" data-consistency-retry>重试</button></div>';
    }
    if (loading) {
      return '<div class="consistency-loading" role="status"><span class="consistency-spinner" aria-hidden="true"></span>' +
        '正在按「' + esc(filterMeta(selectedFilter).label) + '」重新筛选…</div>';
    }
    const findings = currentReport.findings || [];
    if (!findings.length) {
      const total = totalFindings(currentReport);
      if (!total) {
        return '<div class="consistency-empty" role="status"><strong>未发现一致性问题</strong>' +
          '<p>本次检查覆盖七类规则，共核对 ' + esc(numberOrZero(currentReport.totalExamined)) +
          ' 条记录。继续写作后可以随时回来复查。</p></div>';
      }
      return '<div class="consistency-empty" role="status"><strong>当前筛选下没有问题</strong>' +
        '<p>全量检查共发现 ' + esc(total) + ' 个问题，切换到「全部」可以查看完整清单。</p></div>';
    }
    return findings.map(findingHtml).join('');
  }

  function findingsHeadHtml() {
    const report = currentReport;
    const total = report ? totalFindings(report) : 0;
    const returned = report ? (report.findings || []).length : 0;
    const countText = report
      ? '显示 ' + returned + ' / 共 ' + total + ' 个问题'
      : '等待检查结果';
    return '<div class="consistency-findings-head"><div><h3>问题清单</h3>' +
      '<p class="consistency-filter-hint">按严重度累计筛选（选「高危」会同时显示阻断与高危）；' +
      '分数与分类统计始终基于全量检查，不随筛选变化。</p></div>' +
      '<span class="consistency-result-count">' + esc(countText) + '</span></div>' +
      '<div class="consistency-filters" role="group" aria-label="按严重度筛选">' + filtersHtml() + '</div>';
  }

  function renderPanel() {
    const root = mountedRoot;
    if (!root || !root.isConnected) return;
    root.dataset.consistencyState = loading ? 'loading' : (errorText ? 'error' : 'ready');
    root.setAttribute('aria-busy', loading ? 'true' : 'false');
    root.innerHTML = scoreCardHtml() + categoriesHtml() +
      '<section class="consistency-findings" aria-label="问题清单">' + findingsHeadHtml() +
      '<div class="consistency-findings-list">' + findingsListHtml() + '</div></section>';
  }

  function normalizeReport(value) {
    if (!value || typeof value !== 'object') throw new Error('一致性接口没有返回有效报告');
    const score = Number(value.score);
    if (!Number.isFinite(score)) throw new Error('一致性报告缺少分数');
    if (!value.categories || typeof value.categories !== 'object') throw new Error('一致性报告缺少分类统计');
    if (!Array.isArray(value.findings)) throw new Error('一致性报告缺少问题清单');
    return {
      score: score,
      grade: String(value.grade || gradeOf(score)),
      counts: value.counts && typeof value.counts === 'object' ? value.counts : {},
      categories: value.categories,
      examined: value.examined && typeof value.examined === 'object' ? value.examined : {},
      totalExamined: numberOrZero(value.total_examined),
      findings: value.findings,
      returned: numberOrZero(value.returned),
      filteredBy: value.filtered_by || null,
      summary: String(value.summary || ''),
      generatedAt: value.generated_at || '',
      version: value.version == null ? '' : value.version
    };
  }

  function friendlyError(error) {
    const message = String((error && error.message) || error || '').trim();
    const offline = typeof backendOnline !== 'undefined' && backendOnline === false;
    if (!message) return offline ? OFFLINE_TEXT : '一致性检查失败，请稍后重试。';
    if (/failed to fetch|networkerror|load failed|fetch failed|network request failed/i.test(message)) {
      return OFFLINE_TEXT;
    }
    return message;
  }

  async function load(filterKey, options) {
    const settings = options || {};
    const meta = filterMeta(filterKey);
    const token = ++requestSequence;
    selectedFilter = meta.key;
    loading = true;
    errorText = '';
    renderPanel();
    try {
      const query = meta.param ? '?min_severity=' + encodeURIComponent(meta.param) : '';
      const response = await fetch('/api/consistency' + query, { headers: { Accept: 'application/json' } });
      const payload = await response.json().catch(() => null);
      if (token !== requestSequence) return;
      if (!response.ok) {
        throw new Error((payload && payload.error) || ('一致性接口返回 ' + response.status));
      }
      currentReport = normalizeReport(payload);
      currentFilter = meta.key;
      loading = false;
      errorText = '';
      renderPanel();
      if (settings.manual) toast('一致性报告已刷新');
    } catch (error) {
      if (token !== requestSequence) return;
      loading = false;
      errorText = friendlyError(error);
      selectedFilter = currentFilter;
      renderPanel();
      if (settings.manual) toast('一致性检查未完成');
    }
    if (settings.focusFilter) {
      const button = mountedRoot && mountedRoot.querySelector('[data-consistency-filter="' + meta.key + '"]');
      if (button && button.isConnected) button.focus({ preventScroll: true });
    }
  }

  function openChapter(chapterId) {
    const id = safeChapterId(chapterId);
    if (!id) return;
    try {
      activeChapter = id;
      view = 'editor';
      render();
    } catch (error) {
      toast('暂时无法打开该章节');
    }
  }

  function bindRoot(root) {
    root.addEventListener('click', event => {
      const target = event.target;
      if (!target || typeof target.closest !== 'function') return;
      const filterButton = target.closest('[data-consistency-filter]');
      if (filterButton) {
        const key = filterButton.getAttribute('data-consistency-filter');
        if (key && key !== selectedFilter) {
          event.preventDefault();
          load(key, { focusFilter: true });
        }
        return;
      }
      const chapterLink = target.closest('[data-chapter]');
      if (chapterLink) {
        event.preventDefault();
        openChapter(chapterLink.getAttribute('data-chapter'));
      }
    });
  }

  function mount(root) {
    mountedRoot = root;
    bindRoot(root);
    /* 每次重新进入面板都从空报告开始：避免后端已离线时把上一次的分数
       当成这次检查的结果展示。筛选失败时才保留当前报告。 */
    currentReport = null;
    errorText = '';
    renderPanel();
    load(selectedFilter);
  }

  function handleMainChange() {
    const main = document.querySelector('#main');
    if (!main) return;
    const root = main.querySelector('[data-consistency-root]');
    if (!root) {
      mountedRoot = null;
      return;
    }
    if (root === mountedRoot) return;
    mountedRoot = root;
    mount(root);
  }

  /* 视图骨架：render() 每次都会重建 #main，所以这里只做同步占位。 */
  views.consistency = () => '<div class="view-head"><div><h2>一致性检查</h2>' +
    '<p>用确定性规则核对事实、时间线、知识边界、属性、关系、伏笔与来源；只读，不改动任何稿件。</p></div>' +
    '<div class="view-head-actions"><button type="button" class="ghost-btn" data-consistency-refresh>↻ 重新检查</button></div></div>' +
    '<div class="consistency-panel" data-consistency-root aria-live="polite"></div>';

  document.addEventListener('click', event => {
    const target = event.target;
    if (!target || typeof target.closest !== 'function') return;
    if (typeof view === 'undefined' || view !== 'consistency') return;
    const refresh = target.closest('[data-consistency-refresh],[data-consistency-retry]');
    if (!refresh) return;
    event.preventDefault();
    if (!loading) load(selectedFilter, { manual: true });
  });

  function init() {
    const main = document.querySelector('#main');
    if (!main) return;
    if (!observer) {
      observer = new MutationObserver(handleMainChange);
      observer.observe(main, { childList: true, subtree: true });
    }
    handleMainChange();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
