const view = {
  snapshot: null,
  selectedCode: null,
  expandedCode: null,
  activeTab: "all",
  filterValues: {},
  seenAlertIds: new Set(),
  firstLoad: true,
  tabSides: {},
  expandedSectors: new Set(),   // 開いているアコーディオンのkey（ポーリング再描画でも維持）
  sectorTf: "5m",               // アクティブなTFサブタブ
  sectorCatLevel: "big",        // NEEDS分類粒度: "big" | "mid"
  sectorMemberCache: {},
  sectorRenderCache: {
    baseKey: null,
    sampleNote: null,
    heatmapKey: null,
    rotationKey: null,
    bigcatKey: null,
    bindKey: null,
  },
  sectorRenderToken: 0,
  dailyDates: null,  // {day, week_start, week_end} — /api/daily_dates から取得
};

// タイムフレーム別メタデータ
const TF_META = {
  "5m":   { label: "5分",  dir: "直近5分間の騰落率",   vol: "直近5分間の売買代金増分",   pop: "監視銘柄（最大100銘柄）", flowKey: "flow_5m_sum_weighted",  axisNote: "直近5分フロー加重合計",  changeLabel: "5分比",  source: "intraday", api: "/api/sector_flow?tf=5m"       },
  "15m":  { label: "15分", dir: "直近15分間の騰落率",  vol: "直近15分間の売買代金増分",  pop: "監視銘柄（最大100銘柄）", flowKey: "flow_15m_sum_weighted", axisNote: "直近15分フロー加重合計", changeLabel: "15分比", source: "intraday", api: "/api/sector_flow?tf=15m"      },
  "60m":  { label: "60分", dir: "直近60分間の騰落率",  vol: "直近60分間の売買代金増分",  pop: "監視銘柄（最大100銘柄）", flowKey: "flow_60m_sum_weighted", axisNote: "直近60分フロー加重合計", changeLabel: "60分比", source: "intraday", api: "/api/sector_flow?tf=60m"      },
  "day":  { label: "日",   dir: "当日前日比",           vol: "当日 close×volume",         pop: "全市場（日足CSV確定値）",  flowKey: "flow_day_sum_weighted", axisNote: "日中フロー加重合計",     changeLabel: "前日比", source: "daily",    api: "/api/sector_flow_daily?tf=day"  },
  "week": { label: "週間", dir: "直近5日間騰落率",     vol: "5日合計 close×volume",      pop: "全市場（日足CSV確定値）",  flowKey: "flow_day_sum_weighted", axisNote: "週間フロー加重合計",     changeLabel: "週間比", source: "daily",    api: "/api/sector_flow_daily?tf=week" },
};
// 日付を "M/D" 形式に変換（"2026-06-24" → "6/24"）
const fmtMD = (d) => d ? d.replace(/^\d{4}-(\d{2})-(\d{2})$/, (_, m, day) => `${+m}/${+day}`) : "?";

// 日/週間タブのラベルを日付入りで返す
function tfLabel(tf) {
  const m = TF_META[tf];
  if (!m) return tf;
  if (tf === "day" && view.dailyDates?.day) return `日（${fmtMD(view.dailyDates.day)}）`;
  if (tf === "week" && view.dailyDates?.week_start) {
    return `週間（〜${fmtMD(view.dailyDates.week_end)}）`;
  }
  return m.label;
}

// 日/週間の「対象」説明を日付入りで返す
function tfPop(tf) {
  const m = TF_META[tf];
  if (!m) return "";
  if (tf === "day" && view.dailyDates?.day) return `全市場（日足CSV確定値 ${fmtMD(view.dailyDates.day)}）`;
  if (tf === "week" && view.dailyDates?.week_start) {
    return `全市場（日足CSV確定値 ${fmtMD(view.dailyDates.week_start)}〜${fmtMD(view.dailyDates.week_end)}）`;
  }
  return m.pop;
}

// 日足の参照日付を一度だけ取得してキャッシュ
async function fetchDailyDates() {
  if (view.dailyDates) return;
  try {
    const resp = await fetch("/api/daily_dates", { cache: "no-store" });
    if (resp.ok) view.dailyDates = await resp.json();
  } catch (_) {}
}

// 日足API結果を60秒キャッシュ（2秒ポーリングで毎回叩かないため）
const _dailyCache = {};
async function _fetchSectorData(api) {
  if (api.includes("sector_flow_daily")) {
    const now = Date.now();
    if (_dailyCache[api] && now - _dailyCache[api].ts < 60000) return _dailyCache[api].data;
    const resp = await fetch(api, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    _dailyCache[api] = { data, ts: now };
    return data;
  }
  const resp = await fetch(api, { cache: "no-store" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

const $ = (selector) => document.querySelector(selector);

const CONDITIONS = [
  { id: "upper_price", label: "上限価格", unit: "円", config: "upper_price_offset_pct", source: "price", mode: "baseline_up", description: "起動時価格から設定率以上に上昇" },
  { id: "lower_price", label: "下限価格", unit: "円", config: "lower_price_offset_pct", source: "price", mode: "baseline_down", description: "起動時価格から設定率以下に下落" },
  { id: "change_up_pct", label: "前日比上限", unit: "%", config: "change_up_pct", source: "change_pct", mode: "gte", description: "前日比が設定値以上" },
  { id: "change_down_pct", label: "前日比下限", unit: "%", config: "change_down_pct", source: "change_pct", mode: "lte", description: "前日比が設定値以下" },
  { id: "open_up_pct", label: "始値比上限", unit: "%", config: "open_up_pct", source: "from_open", mode: "gte", description: "当日始値比が設定値以上" },
  { id: "open_down_pct", label: "始値比下限", unit: "%", config: "open_down_pct", source: "from_open", mode: "lte", description: "当日始値比が設定値以下" },
  { id: "vwap_up_pct", label: "VWAP比上限", unit: "%", config: "vwap_up_pct", source: "vwap_gap", mode: "gte", description: "VWAP乖離率が設定値以上" },
  { id: "vwap_down_pct", label: "VWAP比下限", unit: "%", config: "vwap_down_pct", source: "vwap_gap", mode: "lte", description: "VWAP乖離率が設定値以下" },
  { id: "year_high_gap_pct", label: "年初来高値差分", unit: "%", config: "year_high_gap_pct", source: "year_high_gap", mode: "near", description: "年初来高値までの差が設定値以内" },
  { id: "listing_high_gap_pct", label: "上場来高値差分", unit: "%", config: "listing_high_gap_pct", source: "listing_high_gap", mode: "near", description: "上場来高値までの差が設定値以内" },
  { id: "year_low_gap_pct", label: "年初来安値差分", unit: "%", config: "year_low_gap_pct", source: "year_low_gap", mode: "near", description: "年初来安値からの差が設定値以内" },
  { id: "listing_low_gap_pct", label: "上場来安値差分", unit: "%", config: "listing_low_gap_pct", source: "listing_low_gap", mode: "near", description: "上場来安値からの差が設定値以内" },
  { id: "premarket_gu_pct", label: "寄前GU上位", unit: "%", config: "premarket_gu_pct", source: "gap_pct", mode: "gte", description: "GU/GD率が設定値以上", filterOnly: true, sort: "gap_desc" },
  { id: "premarket_gd_pct", label: "寄前GD上位", unit: "%", config: "premarket_gd_pct", source: "gap_pct", mode: "lte", description: "GU/GD率が設定値以下", filterOnly: true, sort: "gap_asc" },
];

const number = (value, digits = 0) => value === null || value === undefined || value === ""
  ? "--"
  : Number(value).toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const percent = (value, digits = 2) => value === null || value === undefined || value === ""
  ? "--"
  : `${Number(value) >= 0 ? "+" : ""}${number(Number(value) * 100, digits)}%`;
const tone = (value) => Number(value) > 0 ? "up" : Number(value) < 0 ? "down" : "flat";
// 売買代金はExcelから千円単位で来る
const turnover = (value) => {
  const yen = Number(value || 0) * 1000;
  if (yen >= 1e12) return `${number(yen / 1e12, 2)}兆`;
  if (yen >= 1e8) return `${number(yen / 1e8, 1)}億`;
  if (yen >= 1e4) return `${number(yen / 1e4, 0)}万`;
  return number(yen);
};
// 時価総額は銘柄リストシートから百万円単位で来る
const marketCap = (million) => {
  const m = Number(million);
  if (!Number.isFinite(m) || m <= 0) return "--";
  if (m >= 1e6) return `${number(m / 1e6, 2)}兆円`;
  return `${number(m / 100, 0)}億円`;
};
const rvolText = (value) => {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? `${number(n, 2)}×` : "--";
};
const rvolHot = (value) => {
  const threshold = Number(view.snapshot?.config?.attention_rvol ?? 2);
  const n = Number(value);
  return Number.isFinite(n) && n >= threshold;
};
// HTML escape — prevents display glitch if stock names contain &, <, > or "
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// Paired tab groups — 12 conditions → 6 tabs with an A/B toggle each
// (上限価格・下限価格はタブなし。アラート設定には引き続き使用)
const TAB_GROUPS = [
  { id: "change",    label: "前日比",             sideA: "change_up_pct",        sideB: "change_down_pct",      toggleA: "上限",   toggleB: "下限"   },
  { id: "open",      label: "始値比",             sideA: "open_up_pct",          sideB: "open_down_pct",        toggleA: "上限",   toggleB: "下限"   },
  { id: "high_gap",  label: "YH/LH差分",          sideA: "year_high_gap_pct",    sideB: "listing_high_gap_pct", toggleA: "年初来", toggleB: "上場来" },
  { id: "low_gap",   label: "YL/LL差分",          sideA: "year_low_gap_pct",     sideB: "listing_low_gap_pct",  toggleA: "年初来", toggleB: "上場来" },
  { id: "premarket", label: "寄前",          sideA: "premarket_gu_pct",     sideB: "premarket_gd_pct",     toggleA: "GU上位", toggleB: "GD上位" },
];

const SCREENERS = [
  {
    id: "trend_core",
    label: "🔥真ブレイク",
    title: "🔥真ブレイク",
    summary: "売買代金×RVOL×高値接近の複合スクリーニング",
    note: {
      intro: "高値接近、RVOL、売買代金を土台にしつつ、15分/60分の流れも確認して順張り候補を絞ります。",
      bullets: [
        "売買代金が十分で、RVOLが高い",
        "15分または60分の流れが上向き",
        "高値接近・高値更新・日中高値圏のどれかに該当",
        "VWAP上または始値比プラスで失速感が弱い",
      ],
      rvol_note: "※ RVOLは当日の売買代金を過去平均と比較した倍率。2倍なら平均の2倍の商いが入っている目安。",
    },
  },
  {
    id: "pullback_resume",
    label: "💎押し目再加速",
    title: "💎押し目再加速",
    summary: "上位足が強い銘柄の二段目を狙う、順張りの継続候補",
    note: {
      intro: "60分/15分で強い銘柄のうち、押した後に再び上向き始めたものを抽出します。",
      bullets: [
        "60分・15分が上向き",
        "5分も再びプラス圏",
        "VWAP上を維持",
        "高値圏に近く、商いが落ちていない",
      ],
    },
  },
  {
    id: "reversal_early",
    label: "🍄反転初動",
    title: "🍄反転初動",
    summary: "Mフロー当日流出圏から5分足が先に切り返し始めた銘柄",
    note: {
      intro: "Mフロー当日流出圏（前日比・始値比・VWAP比の合成スコアがマイナス）の銘柄のうち、5分の値動きと短期商いが先に改善した銘柄を拾います。当日流出が深いほど上位に表示されます。",
      bullets: [
        "当日の値動きがMフロー流出圏（前日比×0.5＋始値比×0.3＋VWAP比×0.2 がマイナス）",
        "15分または60分が弱め",
        "5分が明確に陽転し、売買代金増分とRVOLが伴う",
        "板または成行の偏りが買い方向に改善",
      ],
    },
  },
  {
    id: "selling_climax",
    label: "⚡️セリクラ反転",
    title: "⚡️セリクラ反転",
    summary: "大きく売られた後に、短期需給と商いで切り返し始めた銘柄",
    note: {
      intro: "安値圏での投げ売り後、5分の切り返しと商いの急増が出た初動を見ます。",
      bullets: [
        "前日比が大きくマイナス",
        "日中安値圏からの戻りが出ている",
        "5分陽転と5分商い急増",
        "RVOLが高く、板や成行が改善",
      ],
    },
  },
  {
    id: "vwap_reclaim",
    label: "🎯VWAP反発",
    title: "🎯VWAP反発",
    summary: "VWAPに近い水準で需給が先に改善し、反発に転じ始めた銘柄",
    note: {
      intro: "VWAPを割らずに付近で踏みとどまるか、一度近づいてから反発を始めたパターンを拾います。「割れた」は条件にしないため、近づいてきた時点で5分が先行して上向き始めたケースも対象です。",
      bullets: [
        "VWAPより上かつVWAP付近（乖離1.5%以内）",
        "直前の15分または60分の流れが弱め（VWAPに接近してきた痕跡）",
        "5分が陽転し、短期商いが増加",
        "板または成行が買い寄りに改善",
      ],
    },
  },
  {
    id: "theme_leader",
    label: "💛テーマ主役",
    title: "💛テーマ主役",
    summary: "資金流入が強いテーマの中で、個別でも先頭を走っている銘柄",
    note: {
      intro: "テーマフローの追い風がある中で、個別の15分/60分や商いも強い銘柄を優先します。",
      bullets: [
        "強いテーマの上位メンバーに入っている",
        "個別でも15分が上向き",
        "RVOLと売買代金が十分",
        "VWAP下でだらけていない",
      ],
    },
  },
];

const getScreener = (tabId) => SCREENERS.find((screener) => screener.id === tabId);

// #2: Only re-render tab bar when the active tab actually changes
let lastRenderedTab = null;
let lastNoteBodyKey = null;   // スクリーニングノート本体の再描画を抑制
let lastAlertKey = null;       // アラートリストの再描画を抑制
let lastAnalysisKey = null;    // 資金フロータブの再描画を抑制
function renderTabs() {
  if (view.activeTab === lastRenderedTab) return;
  lastRenderedTab = view.activeTab;
  const tabs = [
    { id: "all", label: "全銘柄" },
    { id: "favorites", label: "★銘柄" },
    ...SCREENERS.map(({ id, label }) => ({ id, label })),
    { id: "sector", label: "📕セクター" },
    { id: "analysis", label: "💸Mフロー" },
    ...TAB_GROUPS,
  ];
  $("#view-tabs").innerHTML = tabs.map((tab) =>
    `<button data-tab="${tab.id}" class="${view.activeTab === tab.id ? "active" : ""}">${tab.label}</button>`
  ).join("");
  document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => {
    view.activeTab = button.dataset.tab;
    renderTabs();
    renderFilterPanel();
    renderAll();
  }));
}

function activeCondition() {
  const group = TAB_GROUPS.find((g) => g.id === view.activeTab);
  if (!group) return undefined;
  const side = view.tabSides[group.id] || "A";
  const condId = side === "A" ? group.sideA : group.sideB;
  return CONDITIONS.find((c) => c.id === condId);
}

function configValue(condition) {
  if (view.filterValues[condition.id] !== undefined) return view.filterValues[condition.id];
  return Number(view.snapshot?.config?.[condition.config] ?? 0);
}

function renderScreeningNote(screener) {
  const noteEl = $("#screening-note");
  const bodyEl = $("#screening-note-body");
  if (!screener) {
    noteEl.classList.add("hidden");
    lastNoteBodyKey = null;
    return;
  }
  const cfg = view.snapshot?.config || {};
  const noteKey = `${screener.id}|${cfg.attention_turnover_oku}|${cfg.attention_rvol}`;
  if (noteKey !== lastNoteBodyKey) {
    lastNoteBodyKey = noteKey;
    $("#screening-note-title").textContent = screener.title;
    $("#screening-note-summary").textContent = screener.summary;
    const prefix = screener.id === "trend_core"
      ? `<section><b>【前提】</b><p>当日の売買代金が現時点で <strong>${number(cfg.attention_turnover_oku ?? 10, 0)}</strong>億円以上、RVOLが <strong>${number(cfg.attention_rvol ?? 2, 1)}</strong>倍以上を基本線に見ます。</p></section>`
      : "";
    const bullets = screener.note?.bullets?.length
      ? `<section><ul>${screener.note.bullets.map((item) => `<li>${item}</li>`).join("")}</ul></section>`
      : "";
    const rvolNote = screener.note?.rvol_note
      ? `<section><p class="analysis-note-caution">${screener.note.rvol_note}</p></section>`
      : "";
    bodyEl.innerHTML = `${prefix}<section><p>${screener.note?.intro || ""}</p></section>${bullets}${rvolNote}`;
  }
  noteEl.classList.remove("hidden");
}

function renderFilterPanel() {
  const group = TAB_GROUPS.find((g) => g.id === view.activeTab);
  const condition = activeCondition();
  const screener = getScreener(view.activeTab);
  const filterBody = $("#filter-body");

  // ヘッダーは全タブ共通
  $("#filter-title").textContent = "検索 / ソート";
  $("#filter-summary").textContent = "";
  $("#filter-chevron").textContent = filterBody.classList.contains("hidden") ? "▼" : "▲";

  if (screener) {
    renderScreeningNote(screener);
    $("#filter-value-row").classList.add("hidden");
    return;
  }

  if (!condition) {
    renderScreeningNote(null);
    $("#filter-value-row").classList.add("hidden");
    return;
  }

  // 条件タブ: 青いボックスに条件説明を表示（スクリーナーと統一）
  const value = configValue(condition);
  const unit = condition.mode.startsWith("baseline") ? "%" : condition.unit;
  const cfgVal = view.snapshot?.config?.[condition.config];
  const noteKey = `cond|${condition.id}|${value}|${cfgVal}`;
  if (noteKey !== lastNoteBodyKey) {
    lastNoteBodyKey = noteKey;
    $("#screening-note-title").textContent = group ? group.label : condition.label;
    $("#screening-note-summary").textContent = condition.description;
    $("#screening-note-body").innerHTML = `<section><p>現在の設定値: <strong>${value}${unit}</strong>&emsp;（Config初期値: ${cfgVal}${unit}）</p></section>`;
  }
  $("#screening-note").classList.remove("hidden");

  // filter-value-row
  $("#filter-value-row").classList.remove("hidden");
  $("#filter-label").textContent = condition.mode.startsWith("baseline") ? "起動時価格からの変動率" : condition.label;
  $("#filter-value").value = value;
  $("#filter-unit").textContent = unit;
  $("#filter-description").textContent = `Excel Configの初期値: ${cfgVal}%`;

  // 上限／下限トグル（グループタブのみ）
  const toggleContainer = $("#filter-side-toggle");
  if (group) {
    const side = view.tabSides[group.id] || "A";
    if (toggleContainer.dataset.group !== group.id) {
      toggleContainer.dataset.group = group.id;
      toggleContainer.innerHTML = `<small class="filter-side-label">種別</small>
        <fieldset class="sort-direction">
          <legend>種別</legend>
          <label><input type="radio" name="tab-side" value="A"><span>${group.toggleA}</span></label>
          <label><input type="radio" name="tab-side" value="B"><span>${group.toggleB}</span></label>
        </fieldset>`;
      toggleContainer.querySelectorAll('input[name="tab-side"]').forEach((input) =>
        input.addEventListener("change", () => {
          view.tabSides[group.id] = input.value;
          renderFilterPanel();
          renderAll();
        }));
    }
    toggleContainer.querySelectorAll('input[name="tab-side"]').forEach((input) => {
      input.checked = input.value === side;
    });
    toggleContainer.classList.remove("hidden");
  } else {
    toggleContainer.classList.add("hidden");
  }
}

function screeningThresholds() {
  const cfg = view.snapshot?.config || {};
  return {
    minTurnover: (numericValue(cfg.attention_turnover_oku) ?? 10) * 100000,
    rvol: numericValue(cfg.attention_rvol) ?? 2,
    highGapPct: numericValue(cfg.attention_high_gap_pct) ?? 3,
  };
}

function nearHighBreak(stock) {
  const { highGapPct } = screeningThresholds();
  const yearGap = numericValue(stock.year_high_gap);
  const listingGap = numericValue(stock.listing_high_gap);
  const breaks = view.snapshot?.breaks?.[stock.code];
  const nearHigh = (yearGap !== null && yearGap * 100 <= highGapPct)
    || (listingGap !== null && listingGap * 100 <= highGapPct);
  return nearHigh || Boolean(breaks?.year_high || breaks?.listing_high);
}

function nearLowZone(stock) {
  const yearGap = numericValue(stock.year_low_gap);
  const listingGap = numericValue(stock.listing_low_gap);
  const breaks = view.snapshot?.breaks?.[stock.code];
  return (yearGap !== null && yearGap * 100 <= 3)
    || (listingGap !== null && listingGap * 100 <= 3)
    || Boolean(breaks?.year_low || breaks?.listing_low);
}

function strongestImbalance(stock) {
  const values = [
    numericValue(stock.book_imbalance_3),
    numericValue(stock.book_imbalance),
    numericValue(stock.market_order_imbalance),
    numericValue(stock.auction_imbalance),
  ].filter((value) => value !== null);
  return values.length ? Math.max(...values) : 0;
}

function screeningResult(stock, screenerId) {
  const { minTurnover, rvol: baseRvol } = screeningThresholds();
  const turnover = numericValue(stock.turnover) ?? 0;
  const rvol = numericValue(stock.rvol) ?? 0;
  const changePct = numericValue(stock.change_pct) ?? 0;
  const fromOpen = numericValue(stock.from_open) ?? 0;
  const vwapGap = numericValue(stock.vwap_gap) ?? 0;
  const change5 = numericValue(stock.change_5m) ?? 0;
  const change15 = numericValue(stock.change_15m) ?? 0;
  const change60 = numericValue(stock.change_60m) ?? 0;
  const turnover5 = numericValue(stock.turnover_delta_5m) ?? 0;
  const turnover15 = numericValue(stock.turnover_delta_15m) ?? 0;
  const position = highLowPosition(stock) ?? 0;
  const fromHigh = numericValue(stock.from_high) ?? 0;
  const imbalance = strongestImbalance(stock);
  const pulse5 = turnover > 0 ? turnover5 / turnover : 0;
  const pulse15 = turnover > 0 ? turnover15 / turnover : 0;
  const themeLeader = view.snapshot?.theme_leaders?.[stock.code];
  const nearHigh = nearHighBreak(stock);
  const nearLow = nearLowZone(stock);

  switch (screenerId) {
    case "trend_core": {
      const match = turnover >= minTurnover
        && rvol >= baseRvol
        && (change15 > 0.004 || change60 > 0.008 || changePct > 0.015)
        && (nearHigh || position >= 75)
        && (vwapGap > 0 || fromOpen > 0)
        && imbalance > -0.15;
      const score = rvol * 25 + Math.max(change15, 0) * 800 + Math.max(change60, 0) * 550
        + Math.max(position - 60, 0) * 0.8 + pulse15 * 900 + (nearHigh ? 18 : 0) + imbalance * 30;
      return { match, score };
    }
    case "reversal_early": {
      // Mフロー当日と同じ合成スコアで「流出圏」を判定
      const dayDir = changePct * 0.5 + fromOpen * 0.3 + vwapGap * 0.2;
      const match = turnover >= minTurnover * 0.5
        && (rvol >= 1.2 || pulse5 > 0.05)  // 大型株はRVOL低くても5分バーストで代替
        && change5 > 0.003
        && turnover5 > 0
        && dayDir < -0.002              // Mフロー当日流出と同じ軸でマイナス圏
        && (change15 < 0 || change60 < 0)
        && imbalance > 0               // 板・成行が買い方向（緩めに）
        && vwapGap > -0.02;            // VWAP-2%以内（売られた分を許容）
      const score = change5 * 1200 + pulse5 * 1200 + rvol * 20
        + Math.max(-change15, 0) * 350
        + Math.abs(dayDir) * 1500      // 当日流出が深いほど上位へ
        + imbalance * 55 + (vwapGap > 0 ? 10 : 0);
      return { match, score };
    }
    case "pullback_resume": {
      const match = turnover >= minTurnover * 0.5
        && rvol >= 1.3
        && change60 > 0.008
        && change15 > 0.003
        && change5 > 0.0015
        && vwapGap > 0
        && (nearHigh || position >= 70)
        && fromHigh <= 0.02;
      const score = change60 * 700 + change15 * 900 + change5 * 500 + pulse15 * 850
        + Math.max(position - 55, 0) * 0.75 + (nearHigh ? 16 : 0) + rvol * 18;
      return { match, score };
    }
    case "vwap_reclaim": {
      const match = turnover >= minTurnover * 0.35
        && vwapGap > 0 && vwapGap < 0.015
        && change5 > 0.002
        && turnover5 > 0
        && (change15 < 0.003 || change60 < 0.005)
        && imbalance > 0;
      const score = (0.015 - vwapGap) * 2000 + change5 * 1100 + pulse5 * 1150 + rvol * 16
        + imbalance * 45;
      return { match, score };
    }
    case "selling_climax": {
      const match = turnover >= minTurnover * 0.35
        && rvol >= 1.4
        && changePct <= -0.02
        && change5 > 0.003
        && turnover5 > 0
        && position >= 25 && position <= 65
        && (nearLow || position <= 45)
        && imbalance > 0.02;
      const score = Math.abs(changePct) * 700 + change5 * 1200 + pulse5 * 1400 + rvol * 22
        + (nearLow ? 14 : 0) + imbalance * 60 + (stock.special_quote ? 8 : 0);
      return { match, score };
    }
    case "theme_leader": {
      const rankBoost = themeLeader ? Math.max(0, 4 - (numericValue(themeLeader.theme_rank) ?? 4)) : 0;
      const match = Boolean(themeLeader)
        && turnover >= minTurnover * 0.5
        && rvol >= 1.2
        && change15 > 0.003
        && (change60 > 0 || vwapGap >= 0)
        && imbalance > -0.1;
      const score = rankBoost * 22 + rvol * 15 + change15 * 900 + Math.max(change60, 0) * 500
        + pulse15 * 900 + Math.max(vwapGap, 0) * 500 + (numericValue(themeLeader?.theme_breadth) ?? 0) * 20;
      return { match, score };
    }
    default:
      return { match: false, score: 0 };
  }
}

function matchesCondition(stock, condition) {
  const threshold = configValue(condition);
  const sourceValue = stock[condition.source];
  if (sourceValue === null || sourceValue === undefined || sourceValue === "") return false;
  const raw = Number(sourceValue);
  if (!Number.isFinite(raw)) return false;
  if (condition.mode === "baseline_up" || condition.mode === "baseline_down") {
    const baseline = Number(view.snapshot.baseline_prices[stock.code]);
    if (!baseline) return false;
    const change = ((raw / baseline) - 1) * 100;
    return condition.mode === "baseline_up" ? change >= threshold : change <= threshold;
  }
  const value = raw * 100;
  if (condition.mode === "gte") return value >= threshold;
  if (condition.mode === "lte") return value <= threshold;
  return value <= Math.abs(threshold);
}

function filteredStocks() {
  if (!view.snapshot) return [];
  const query = $("#search").value.trim().toLowerCase();
  const rules = view.snapshot.rules || {};
  const favorites = new Set(view.snapshot.favorites || []);
  const condition = activeCondition();
  const screener = getScreener(view.activeTab);
  const screeningScores = new Map();
  let stocks = view.snapshot.stocks.filter((stock) => {
    const searchMatch = !query || stock.code.toLowerCase().includes(query)
      || String(stock.name || "").toLowerCase().includes(query);
    const alertMatch = !$("#only-alerts").checked || Boolean(rules[stock.code]?.enabled);
    const screening = screener ? screeningResult(stock, screener.id) : null;
    if (screening) screeningScores.set(stock.code, screening.score);
    const tabMatch = view.activeTab === "favorites"
      ? favorites.has(stock.code)
      : screener
        ? screening.match
        : condition ? matchesCondition(stock, condition) : true;
    return searchMatch && alertMatch && tabMatch;
  });
  if (screener) {
    stocks.sort((a, b) =>
      (screeningScores.get(b.code) ?? 0) - (screeningScores.get(a.code) ?? 0)
      || (numericValue(b.turnover) ?? 0) - (numericValue(a.turnover) ?? 0));
    return stocks;
  }
  const fixedSort = condition?.sort;
  const sortField = fixedSort?.startsWith("gap_") ? "gap_pct" : $("#sort").value;
  const direction = fixedSort?.endsWith("_asc")
    ? "asc"
    : fixedSort?.endsWith("_desc")
      ? "desc"
      : document.querySelector('input[name="sort-direction"]:checked')?.value || "desc";
  stocks.sort((a, b) => {
    const aValue = numericValue(a[sortField]) ?? 0;
    const bValue = numericValue(b[sortField]) ?? 0;
    return direction === "asc" ? aValue - bValue : bValue - aValue;
  });
  return stocks;
}

function renderSummaryCards(stocks) {
  $("#analysis-change-up").textContent = number(stocks.filter((s) => (numericValue(s.change_pct) ?? 0) > 0).length);
  $("#analysis-change-down").textContent = number(stocks.filter((s) => (numericValue(s.change_pct) ?? 0) < 0).length);
  $("#analysis-open-up").textContent = number(stocks.filter((s) => (numericValue(s.from_open) ?? 0) > 0).length);
  $("#analysis-open-down").textContent = number(stocks.filter((s) => (numericValue(s.from_open) ?? 0) < 0).length);
}

function highLowPosition(stock) {
  const low = Number(stock.low), high = Number(stock.high), price = Number(stock.price);
  if (!low || !high || high === low) return null;
  return Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100));
}

// 銘柄名の横に出すバッジ（本日ブレイク・決算接近・特別気配）
function rowBadges(stock) {
  const breaks = view.snapshot?.breaks?.[stock.code];
  let html = "";
  const highCount = (breaks?.year_high?.count || 0) + (breaks?.listing_high?.count || 0);
  const lowCount = (breaks?.year_low?.count || 0) + (breaks?.listing_low?.count || 0);
  if (highCount) html += `<span class="badge badge-up">高値更新${highCount > 1 ? "×" + highCount : ""}</span>`;
  if (lowCount) html += `<span class="badge badge-down">安値更新${lowCount > 1 ? "×" + lowCount : ""}</span>`;
  const days = numericValue(stock.days_to_earnings);
  if (days !== null && days >= 0 && days <= 1) html += `<span class="badge badge-earnings">${days === 0 ? "本日決算" : "明日決算"}</span>`;
  if (stock.special_quote) html += `<span class="badge badge-special">${esc(stock.special_quote)}</span>`;
  return html;
}

function stockNameHtml(stock) {
  return `<strong>${esc(stock.code)}</strong><span>${esc(stock.name)}</span>${rowBadges(stock)}`;
}

// #1: Build a single row's HTML (used for full rebuild)
function buildRowHtml(stock, rules, favorites) {
  const position = highLowPosition(stock);
  const rule = rules[stock.code];
  const rowClass = [stock.code === view.selectedCode ? "selected" : "", stock.code === view.expandedCode ? "card" : ""].filter(Boolean).join(" ");
  return `<tr data-code="${esc(stock.code)}" class="${rowClass}">
    <td data-label="お気に入り"><button class="star-button ${favorites.has(stock.code) ? "active" : ""}" data-star="${esc(stock.code)}" title="お気に入り">★</button></td>
    <td data-label="銘柄"><div class="stock-name">${stockNameHtml(stock)}</div><small>${esc(stock.market)} ${esc(stock.margin_type)}</small></td>
    <td data-label="現在値" class="number price ${tone(stock.change_pct)}">${esc(stock.tick)} ${number(stock.price)}</td>
    <td data-label="前日比" class="number ${tone(stock.change_pct)}">${percent(stock.change_pct)}</td>
    <td data-label="始値比" class="number ${tone(stock.from_open)}">${percent(stock.from_open)}</td>
    <td data-label="VWAP比" class="number ${tone(stock.vwap_gap)}">${percent(stock.vwap_gap)}</td>
    <td data-label="GU/GD" class="number ${tone(stock.gap_pct)}">${percent(stock.gap_pct)}</td>
    <td data-label="高安位置" class="number">${position === null ? "--" : `<div class="range-bar"><i style="width:${position}%"></i><span>${number(position)}%</span></div>`}</td>
    <td data-label="売買代金" class="number">${turnover(stock.turnover)}</td>
    <td data-label="RVOL" class="number ${rvolHot(stock.rvol) ? "up" : ""}">${rvolText(stock.rvol)}</td>
    <td data-label="通知"><button class="alert-switch ${rule?.enabled ? "active" : ""}" data-alert-toggle="${esc(stock.code)}" title="アラートON/OFF"><i></i></button></td>
    <td data-label="設定"><button class="row-settings" data-settings="${esc(stock.code)}" title="詳細設定">⚙</button></td>
  </tr>`;
}

// #1: Mutate an existing row's cells in-place — no DOM node removal/creation
function updateRowInPlace(row, stock, rules, favorites) {
  const position = highLowPosition(stock);
  const rule = rules[stock.code];
  row.classList.toggle("selected", stock.code === view.selectedCode);
  row.classList.toggle("card", stock.code === view.expandedCode);
  row.querySelector("[data-star]").classList.toggle("active", favorites.has(stock.code));
  const c = row.cells;
  c[2].className = `number price ${tone(stock.change_pct)}`;
  c[2].textContent = `${stock.tick || ""} ${number(stock.price)}`;
  c[3].className = `number ${tone(stock.change_pct)}`;
  c[3].textContent = percent(stock.change_pct);
  c[4].className = `number ${tone(stock.from_open)}`;
  c[4].textContent = percent(stock.from_open);
  c[5].className = `number ${tone(stock.vwap_gap)}`;
  c[5].textContent = percent(stock.vwap_gap);
  c[6].className = `number ${tone(stock.gap_pct)}`;
  c[6].textContent = percent(stock.gap_pct);
  c[7].innerHTML = position === null ? "--" : `<div class="range-bar"><i style="width:${position}%"></i><span>${number(position)}%</span></div>`;
  c[8].textContent = turnover(stock.turnover);
  c[9].className = `number ${rvolHot(stock.rvol) ? "up" : ""}`;
  c[9].textContent = rvolText(stock.rvol);
  // バッジは変化したときだけ銘柄名セルを書き換える
  const badges = rowBadges(stock);
  if (row.dataset.badges !== badges) {
    row.dataset.badges = badges;
    row.querySelector(".stock-name").innerHTML = stockNameHtml(stock);
  }
  row.querySelector("[data-alert-toggle]").classList.toggle("active", Boolean(rule?.enabled));
}

const isMobile = () => window.matchMedia("(max-width: 700px)").matches;

// #1: Attach click/star/alert/settings listeners after a full rebuild
function attachRowListeners(tbody) {
  tbody.querySelectorAll("tr").forEach((row) =>
    row.addEventListener("click", () => {
      if (isMobile()) {
        const code = row.dataset.code;
        if (view.expandedCode === code) {
          view.expandedCode = null;
          row.classList.remove("card");
        } else {
          if (view.expandedCode) {
            const prev = tbody.querySelector(`tr[data-code="${view.expandedCode}"]`);
            if (prev) prev.classList.remove("card");
          }
          view.expandedCode = code;
          row.classList.add("card");
          row.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      } else {
        selectStock(row.dataset.code);
      }
    }));
  tbody.querySelectorAll("[data-star]").forEach((button) =>
    button.addEventListener("click", (event) => { event.stopPropagation(); toggleFavorite(button.dataset.star); }));
  tbody.querySelectorAll("[data-alert-toggle]").forEach((button) =>
    button.addEventListener("click", (event) => { event.stopPropagation(); toggleAlert(button.dataset.alertToggle); }));
  tbody.querySelectorAll("[data-settings]").forEach((button) =>
    button.addEventListener("click", (event) => { event.stopPropagation(); view.selectedCode = button.dataset.settings; openAlertDialog(); }));
}

// #1: Diff-based render — full rebuild only when row order/set changes
function renderTable(stocks) {
  const rules = view.snapshot.rules || {};
  const favorites = new Set(view.snapshot.favorites || []);
  const tbody = $("#stock-rows");
  const existingRows = Array.from(tbody.querySelectorAll("tr"));
  const newCodes = stocks.map((s) => s.code);
  const existingCodes = existingRows.map((r) => r.dataset.code);
  const sameShape = existingRows.length > 0 &&
    newCodes.length === existingCodes.length &&
    newCodes.every((code, i) => code === existingCodes[i]);
  if (sameShape) {
    stocks.forEach((stock, i) => updateRowInPlace(existingRows[i], stock, rules, favorites));
    return;
  }
  tbody.innerHTML = stocks.map((s) => buildRowHtml(s, rules, favorites)).join("");
  attachRowListeners(tbody);
}

function renderAll() {
  const analysisActive = view.activeTab === "analysis";
  const sectorActive = view.activeTab === "sector";
  const specialTab = analysisActive || sectorActive;
  const allStocks = view.snapshot?.stocks || [];
  const stocks = specialTab ? allStocks : filteredStocks();
  renderSummaryCards(stocks);
  $(".filter-panel").classList.toggle("hidden", specialTab);
  $("#monitor-content").classList.toggle("hidden", specialTab);
  $("#analysis-content").classList.toggle("hidden", !analysisActive);
  $("#sector-content").classList.toggle("hidden", !sectorActive);
  if (analysisActive) { renderAnalysis(); return; }
  if (sectorActive) { renderSector(); return; }
  renderTable(stocks);
}

// ─── Squarified Treemap layout ────────────────────────────────────────────
function squarifiedTreemap(items, x, y, w, h) {
  const totalVal = items.reduce((s, d) => s + d.val, 0);
  if (!totalVal || !items.length) return [];
  const nodes = items.map(d => ({ ...d, _a: d.val / totalVal * w * h }));
  const result = [];

  function worst(row, s) {
    const ra = row.reduce((a, b) => a + b._a, 0);
    const maxA = Math.max(...row.map(d => d._a));
    const minA = Math.min(...row.map(d => d._a));
    return Math.max(s * s * maxA / (ra * ra), ra * ra / (s * s * minA));
  }
  function place(row, rx, ry, rw, rh) {
    const ra = row.reduce((a, b) => a + b._a, 0);
    const hz = rw >= rh;
    const strip = hz ? ra / rh : ra / rw;
    let pos = hz ? ry : rx;
    for (const d of row) {
      const len = d._a / strip;
      result.push(hz ? { ...d, rx, ry: pos, rw: strip, rh: len }
                     : { ...d, rx: pos, ry, rw: len, rh: strip });
      pos += len;
    }
    return hz ? { rx: rx + strip, ry, rw: rw - strip, rh }
              : { rx, ry: ry + strip, rw, rh: rh - strip };
  }
  function sq(ns, rx, ry, rw, rh) {
    if (!ns.length || rw < 0.5 || rh < 0.5) return;
    const s = Math.min(rw, rh);
    let row = [];
    for (let i = 0; i < ns.length; i++) {
      const next = [...row, ns[i]];
      if (!row.length || worst(next, s) <= worst(row, s)) { row = next; continue; }
      sq(ns.slice(i), ...Object.values(place(row, rx, ry, rw, rh)));
      return;
    }
    place(row, rx, ry, rw, rh);
  }
  sq(nodes, x, y, w, h);
  return result;
}

// ─── テーマヒートマップ描画 ───────────────────────────────────────────────
function renderSectorHeatmap(data, meta) {
  const wrap = $("#sector-heatmap-wrap");
  if (!wrap) return;
  const fk = meta.flowKey;
  const themes = Object.entries(data.themes || {})
    .filter(([, t]) => t.stock_count > 0 && (t.turnover_sum_raw > 0 || t.turnover_sum_weighted > 0));
  if (!themes.length) {
    wrap.innerHTML = '<div class="empty-state" style="height:200px;display:flex;align-items:center;justify-content:center">テーマデータなし</div>';
    return;
  }
  const W = Math.max(wrap.clientWidth - 2, 300);
  const H = 250;
  const maxAbs = Math.max(...themes.map(([, t]) => Math.abs(t[fk] || 0)), 1);
  const items = themes
    .map(([tid, t]) => ({
      tid, name: t.name,
      val: t.turnover_sum_raw || t.turnover_sum_weighted || 0.001,
      rel: Math.round((t[fk] || 0) / maxAbs * 100),
      count: t.stock_count,
    }))
    .filter(d => d.val > 0)
    .sort((a, b) => b.val - a.val);

  const rects = squarifiedTreemap(items, 0, 0, W, H);

  const defs = rects.map((r, i) =>
    `<clipPath id="hm-cp-${i}"><rect x="${(r.rx+2).toFixed(1)}" y="${(r.ry+2).toFixed(1)}"` +
    ` width="${Math.max(r.rw-4,0).toFixed(1)}" height="${Math.max(r.rh-4,0).toFixed(1)}"/></clipPath>`
  ).join('');

  const tiles = rects.map((r, i) => {
    const { rx, ry, rw, rh, rel, name, count, tid } = r;
    if (rw < 2 || rh < 2) return '';
    const key = `t:${tid}`;
    const isOpen = view.expandedSectors.has(key);
    const rgb = rel > 0 ? "255,101,119" : rel < 0 ? "70,227,155" : "100,115,135";
    const alpha = Math.max(0.12, Math.abs(rel) / 100 * 0.83 + 0.12).toFixed(3);
    const strokeColor = isOpen ? "rgba(85,215,255,0.9)" : `rgba(${rgb},0.28)`;
    const strokeW = isOpen ? 2 : 1;
    const showName  = rw >= 40 && rh >= 18;
    const showScore = rw >= 38 && rh >= 34;
    const showCount = rw >= 54 && rh >= 48;
    const fs = Math.min(Math.max(8, Math.min(rw / 7, rh / 3)), 13);
    const cx = (rx + rw / 2).toFixed(1);
    const cy = (ry + rh / 2).toFixed(1);
    const lines = [
      showName  ? { txt: name, s: fs, c: "rgba(238,243,248,.95)", w: 600, f: "'Yu Gothic UI',sans-serif" } : null,
      showScore ? { txt: (rel > 0 ? "+" : "") + rel, s: Math.min(fs + 1, 14), c: `rgba(${rgb},.9)`, w: 700, f: "Consolas,monospace" } : null,
      showCount ? { txt: `${count}銘柄`, s: 9, c: "rgba(132,145,163,.75)", w: 400, f: "'Yu Gothic UI',sans-serif" } : null,
    ].filter(Boolean);
    const lineH = fs * 1.4;
    const startY = parseFloat(cy) - (lines.length - 1) * lineH / 2;
    const textEls = lines.map((l, j) =>
      `<text x="${cx}" y="${(startY + j * lineH).toFixed(1)}" text-anchor="middle" dominant-baseline="middle"` +
      ` fill="${l.c}" font-size="${l.s}" font-weight="${l.w}" font-family="${l.f}">${esc(l.txt)}</text>`
    ).join('');
    return `<g data-tid="${esc(tid)}" data-rgb="${rgb}" style="cursor:pointer">` +
      `<rect x="${(rx+.5).toFixed(1)}" y="${(ry+.5).toFixed(1)}" width="${(rw-1).toFixed(1)}" height="${(rh-1).toFixed(1)}"` +
      ` fill="rgba(${rgb},${alpha})" stroke="${strokeColor}" stroke-width="${strokeW}" rx="2"/>` +
      `<g clip-path="url(#hm-cp-${i})">${textEls}</g>` +
      `</g>`;
  }).join('');

  wrap.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="display:block;max-width:100%">` +
    `<defs>${defs}</defs>` +
    `<rect width="${W}" height="${H}" fill="var(--panel-2)"/>` +
    `${tiles}</svg>`;

  wrap.querySelectorAll('g[data-tid]').forEach(g => {
    g.addEventListener('click', () => {
      const key = `t:${g.dataset.tid}`;
      const wasOpen = view.expandedSectors.has(key);
      if (wasOpen) view.expandedSectors.delete(key);
      else         view.expandedSectors.add(key);
      const nowOpen = !wasOpen;
      const rect = g.querySelector('rect');
      if (rect) {
        rect.setAttribute('stroke', nowOpen ? 'rgba(85,215,255,0.9)' : `rgba(${g.dataset.rgb},0.28)`);
        rect.setAttribute('stroke-width', nowOpen ? '2' : '1');
      }
      const accRow   = document.querySelector(`#sector-rotation-bars [data-acckey="${key}"]`);
      const accPanel = document.querySelector(`#sector-rotation-bars [data-acc="${key}"]`);
      if (accRow && accPanel) {
        accPanel.classList.toggle("hidden", !nowOpen);
        accRow.classList.toggle("expanded", nowOpen);
        if (nowOpen) loadSectorMembers(accPanel, "theme", g.dataset.tid, meta);
      }
    });
  });
}

async function loadSectorMembers(panel, kind, id, meta) {
  if (!panel || panel.dataset.loadedKey === `${view.sectorTf}:${kind}:${id}`) return;
  const source = meta.source || "intraday";
  const cacheKey = `${source}|${view.sectorTf}|${kind}|${id}`;
  const now = Date.now();
  const ttl = source === "daily" ? 60000 : 5000;
  try {
    let data = view.sectorMemberCache[cacheKey];
    if (!data || now - data.ts > ttl) {
      const url = `/api/sector_flow_members?source=${encodeURIComponent(source)}` +
        `&tf=${encodeURIComponent(view.sectorTf)}` +
        `&kind=${encodeURIComponent(kind)}` +
        `&id=${encodeURIComponent(id)}`;
      const resp = await fetch(url, { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      data = { ts: now, payload: await resp.json() };
      view.sectorMemberCache[cacheKey] = data;
    }
    panel.innerHTML = sectorMembersHtml(data.payload.members, meta.changeLabel);
    panel.dataset.loadedKey = `${view.sectorTf}:${kind}:${id}`;
    panel.querySelectorAll("[data-member-code]").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        view.activeTab = "all";
        selectStock(b.dataset.memberCode);
        renderTabs();
        renderFilterPanel();
      });
    });
  } catch (e) {
    panel.innerHTML = `<div class="sector-acc-empty">所属銘柄の取得エラー: ${esc(String(e))}</div>`;
  }
}

async function renderSector() {
  await fetchDailyDates();
  const renderToken = ++view.sectorRenderToken;
  const meta = TF_META[view.sectorTf] || TF_META["15m"];
  const cache = view.sectorRenderCache;
  // updated_at は textContent 更新のみなので毎ポーリング直接更新（innerHTML 再構築不要）
  $("#sector-updated-at").textContent = view.snapshot?.updated_at
    ? `更新 ${view.snapshot.updated_at.slice(11, 19)}`
    : "";

  // baseKey は TF・分類粒度・日付ラベルにのみ依存（updated_at は含めない）
  const baseKey = `${view.sectorTf}|${view.sectorCatLevel}|${view.dailyDates?.day || ""}|${view.dailyDates?.week_start || ""}`;

  if (cache.baseKey !== baseKey) {
    const subtabEl = $("#sector-subtabs");
    subtabEl.innerHTML = Object.entries(TF_META).map(([tf]) =>
      `<button class="sector-subtab${view.sectorTf === tf ? " active" : ""}" data-subtf="${esc(tf)}">${esc(tfLabel(tf))}</button>`
    ).join("");
    subtabEl.querySelectorAll("[data-subtf]").forEach((btn) =>
      btn.addEventListener("click", () => {
        if (view.sectorTf === btn.dataset.subtf) return;
        view.sectorTf = btn.dataset.subtf;
        renderSector();
      })
    );

    const noteEl = $("#sector-sample-note");
    noteEl.innerHTML =
      `<span class="tf-note-item"><span class="tf-note-label">方向</span>${esc(meta.dir)}</span>` +
      '<span class="tf-note-sep">｜</span>' +
      `<span class="tf-note-item"><span class="tf-note-label">代金</span>${esc(meta.vol)}</span>` +
      '<span class="tf-note-sep">｜</span>' +
      `<span class="tf-note-item"><span class="tf-note-label">対象</span>${esc(tfPop(view.sectorTf))}</span>`;

    $("#sector-theme-axis").innerHTML =
      `<span class="axis-out">← 売られ</span> &nbsp;|&nbsp; <span class="axis-in">買われ →</span>` +
      `&nbsp;&nbsp;（軸: ${esc(meta.axisNote)} ／ 右端=相対強度 最大±100）`;

    const toggleEl = $("#sector-catlevel-toggle");
    toggleEl.innerHTML = [["big", "大分類"], ["mid", "中分類"]]
      .map(([lv, lbl]) =>
        `<button class="catlevel-btn${view.sectorCatLevel === lv ? " active" : ""}" data-lv="${lv}">${lbl}</button>`)
      .join("");
    toggleEl.querySelectorAll("[data-lv]").forEach((btn) =>
      btn.addEventListener("click", () => {
        if (view.sectorCatLevel === btn.dataset.lv) return;
        view.sectorCatLevel = btn.dataset.lv;
        renderSector();
      })
    );
    $("#sector-bigcat-title").textContent =
      `NEEDS${view.sectorCatLevel === "big" ? "大分類" : "中分類"} 買われ／売られ`;
    cache.sampleNote = null;  // TF切替時にサンプルノートを再描画させる
    cache.baseKey = baseKey;
  }

  let data;
  try {
    data = await _fetchSectorData(meta.api);
  } catch (e) {
    if (renderToken !== view.sectorRenderToken) return;
    $("#sector-rotation-bars").innerHTML = `<div class="empty-state">取得エラー: ${esc(String(e))}</div>`;
    return;
  }
  if (renderToken !== view.sectorRenderToken) return;

  if (data.sample_note !== cache.sampleNote) {
    cache.sampleNote = data.sample_note || null;
    const noteEl = $("#sector-sample-note");
    const old = noteEl.querySelector(".tf-note-api");
    if (old) old.remove();
    if (data.sample_note) {
      const span = document.createElement("span");
      span.className = "tf-note-api";
      span.textContent = data.sample_note;
      noteEl.appendChild(span);
    }
  }

  const heatmapKey = JSON.stringify({
    tf: view.sectorTf,
    open: [...view.expandedSectors].filter((k) => k.startsWith("t:")).sort(),
    themes: Object.entries(data.themes || {}).map(([tid, t]) => [tid, t[meta.flowKey], t.turnover_sum_raw, t.turnover_sum_weighted, t.stock_count]),
  });
  if (cache.heatmapKey !== heatmapKey) {
    renderSectorHeatmap(data, meta);
    cache.heatmapKey = heatmapKey;
  }

  const themes = Object.entries(data.themes || {});
  if (!themes.length) {
    if (cache.rotationKey !== "empty") {
      $("#sector-rotation-bars").innerHTML = '<div class="empty-state">テーマデータがありません</div>';
      cache.rotationKey = "empty";
    }
  } else {
    const fk = meta.flowKey;
    const maxAbs = Math.max(...themes.map(([, t]) => Math.abs(t[fk] || 0)), 1);
    const sorted = themes
      .filter(([, t]) => t.stock_count > 0)
      .sort((a, b) => Math.abs(b[1][fk] || 0) - Math.abs(a[1][fk] || 0));

    const catOrder = ["AI・半導体", "防衛・国策", "部品・素材", "エネルギー", "金融"];
    const byCat = {};
    sorted.forEach(([tid, t]) => {
      const cat = t.category || "Other";
      (byCat[cat] = byCat[cat] || []).push([tid, t]);
    });

    const renderThemeRows = (entries) => entries.map(([tid, t]) => {
      const f = t[fk] || 0;
      const pct = Math.min(Math.abs(f) / maxAbs * 50, 50);
      const dir = f >= 0 ? "up" : "down";
      const ml = f >= 0 ? 50 : 50 - pct;
      const rel = Math.round(f / maxAbs * 100);
      const relTxt = (rel > 0 ? "+" : "") + rel;
      const relCls = rel > 0 ? "up" : rel < 0 ? "down" : "flat";
      const accKey = `t:${tid}`;
      const accOpen = view.expandedSectors.has(accKey);
      return `<div class="sector-bar-row ${accOpen ? "expanded" : ""}" data-acckey="${esc(accKey)}">
        <span class="sector-toggle">▶</span>
        <span class="sector-theme-name">${esc(t.name)}</span>
        <span class="sector-count">${t.stock_count}銘柄</span>
        <div class="sector-bar-track">
          <div class="sector-bar ${dir}" style="width:${pct.toFixed(1)}%;margin-left:${ml.toFixed(1)}%"></div>
          <div class="sector-bar-center"></div>
        </div>
        <span class="sector-strength ${relCls}" title="相対強度 最大±100">${relTxt}</span>
      </div>
      <div class="sector-accordion ${accOpen ? "" : "hidden"}" data-acc="${esc(accKey)}" data-kind="theme" data-id="${esc(tid)}">
        <div class="sector-acc-empty">読み込み中…</div>
      </div>`;
    }).join("");

    let html = "";
    catOrder.forEach((cat) => {
      if (!byCat[cat]?.length) return;
      html += `<div class="sector-cat-label">${esc(cat)}</div>`;
      html += renderThemeRows(byCat[cat]);
    });
    Object.keys(byCat).filter((cat) => !catOrder.includes(cat)).forEach((cat) => {
      html += `<div class="sector-cat-label">${esc(cat)}</div>`;
      html += renderThemeRows(byCat[cat]);
    });

    const rotationKey = JSON.stringify({
      tf: view.sectorTf,
      open: [...view.expandedSectors].filter((k) => k.startsWith("t:")).sort(),
      rows: sorted.map(([tid, t]) => [tid, t[fk], t.stock_count, t.category]),
    });
    if (cache.rotationKey !== rotationKey) {
      $("#sector-rotation-bars").innerHTML = html || '<div class="empty-state">テーマデータがありません</div>';
      cache.rotationKey = rotationKey;
    }
  }

  const catData = view.sectorCatLevel === "big" ? (data.big_cats || {}) : (data.mid_cats || {});
  const bigcats = Object.entries(catData).filter(([, b]) => b.turnover_sum > 0);
  if (!bigcats.length) {
    if (cache.bigcatKey !== "empty") {
      $("#sector-bigcat-bars").innerHTML = '<div class="empty-state">データがありません</div>';
      cache.bigcatKey = "empty";
    }
  } else {
    const bcFlowKey = {
      "5m": "flow_5m_sum", "15m": "flow_15m_sum", "60m": "flow_60m_sum",
      "day": "flow_day_sum", "week": "flow_day_sum",
    }[view.sectorTf] || "flow_day_sum";
    const maxAbsBig = Math.max(...bigcats.map(([, b]) => Math.abs(b[bcFlowKey] || 0)), 1);
    const sorted2 = [...bigcats].sort((a, b) => (b[1][bcFlowKey] || 0) - (a[1][bcFlowKey] || 0));
    const html2 = sorted2.map(([cat, b]) => {
      const fv = b[bcFlowKey] || 0;
      const pct = Math.min(Math.abs(fv) / maxAbsBig * 50, 50);
      const dir = fv >= 0 ? "up" : "down";
      const ml = fv >= 0 ? 50 : 50 - pct;
      const rel = Math.round(fv / maxAbsBig * 100);
      const relTxt = (rel > 0 ? "+" : "") + rel;
      const relCls = rel > 0 ? "up" : rel < 0 ? "down" : "flat";
      const accKey = `b:${cat}`;
      const accOpen = view.expandedSectors.has(accKey);
      return `<div class="sector-bigcat-row ${accOpen ? "expanded" : ""}" data-acckey="${esc(accKey)}">
        <span class="sector-toggle">▶</span>
        <span class="sector-bigcat-name">${esc(cat)}</span>
        <div class="sector-bar-track">
          <div class="sector-bar ${dir}" style="width:${pct.toFixed(1)}%;margin-left:${ml.toFixed(1)}%"></div>
          <div class="sector-bar-center"></div>
        </div>
        <span class="sector-strength ${relCls}" title="相対強度 最大±100">${relTxt}</span>
        <span class="sector-bigcat-cnt">${b.stock_count}銘柄</span>
      </div>
      <div class="sector-accordion ${accOpen ? "" : "hidden"}" data-acc="${esc(accKey)}" data-kind="${view.sectorCatLevel}" data-id="${esc(cat)}">
        <div class="sector-acc-empty">読み込み中…</div>
      </div>`;
    }).join("");
    const bigcatKey = JSON.stringify({
      tf: view.sectorTf,
      level: view.sectorCatLevel,
      open: [...view.expandedSectors].filter((k) => k.startsWith("b:")).sort(),
      rows: sorted2.map(([cat, b]) => [cat, b[bcFlowKey], b.stock_count]),
    });
    if (cache.bigcatKey !== bigcatKey) {
      $("#sector-bigcat-bars").innerHTML = html2;
      cache.bigcatKey = bigcatKey;
    }
  }

  const bindKey = `${cache.rotationKey}|${cache.bigcatKey}`;
  if (cache.bindKey !== bindKey) {
    document.querySelectorAll("#sector-content [data-acckey]").forEach((row) => {
      row.addEventListener("click", () => {
        const key = row.dataset.acckey;
        const acc = [...document.querySelectorAll("#sector-content [data-acc]")]
          .find((a) => a.dataset.acc === key);
        if (!acc) return;
        if (view.expandedSectors.has(key)) {
          view.expandedSectors.delete(key);
          acc.classList.add("hidden");
          row.classList.remove("expanded");
        } else {
          view.expandedSectors.add(key);
          acc.classList.remove("hidden");
          row.classList.add("expanded");
          loadSectorMembers(acc, acc.dataset.kind, acc.dataset.id, meta);
        }
        cache.heatmapKey = null;
        cache.rotationKey = null;
        cache.bigcatKey = null;
      });
    });
    cache.bindKey = bindKey;
  }

  document.querySelectorAll("#sector-content [data-acc]:not(.hidden)").forEach((acc) =>
    loadSectorMembers(acc, acc.dataset.kind, acc.dataset.id, meta));
}

function sectorMembersHtml(members, changeLabel = "騰落率") {
  if (!members || !members.length) return '<div class="sector-acc-empty">所属銘柄なし</div>';
  const head = `<div class="sector-member sector-member-head">
    <span class="m-code">コード</span><span class="m-name">銘柄</span>
    <span class="m-change">${esc(changeLabel)}</span><span class="m-turnover">売買代金</span>
  </div>`;
  const rows = members.map((m) => {
    const chg = m.change_tf ?? m.change_pct;
    return `
    <button class="sector-member" data-member-code="${esc(m.code)}">
      <span class="m-code">${esc(m.code)}</span>
      <span class="m-name">${esc(m.name)}</span>
      <span class="m-change ${tone(chg)}">${percent(chg)}</span>
      <span class="m-turnover">${turnover(m.turnover)}</span>
    </button>`;
  }).join("");
  return head + rows;
}

function numericValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function flowRows(stocks) {
  const rows = stocks.map((stock) => {
    const traded = numericValue(stock.turnover);
    const change = numericValue(stock.change_pct);
    const fromOpen = numericValue(stock.from_open);
    const vwapGap = numericValue(stock.vwap_gap);
    if (traded === null || traded <= 0 || change === null || fromOpen === null || vwapGap === null) return null;
    const direction = change * 0.5 + fromOpen * 0.3 + vwapGap * 0.2;
    return { stock, rawScore: traded * direction };
  }).filter(Boolean);
  const maxMagnitude = Math.max(...rows.map((row) => Math.abs(row.rawScore)), 1);
  return rows.map((row) => ({ ...row, score: row.rawScore / maxMagnitude * 100 }));
}

// #3: esc() applied to stock.code / stock.name in analysis ranking HTML
function flowRankingHtml(rows, direction) {
  const selected = rows
    .filter((row) => direction === "in" ? row.score > 0 : row.score < 0)
    .sort((a, b) => direction === "in" ? b.score - a.score : a.score - b.score)
    .slice(0, 10);
  if (!selected.length) return `<div class="empty-state">該当データがありません。</div>`;
  return selected.map((row, index) => {
    const stock = row.stock;
    return `<button class="flow-row" data-analysis-code="${esc(stock.code)}">
      <b>${index + 1}</b>
      <span class="flow-stock"><strong>${esc(stock.code)} ${esc(stock.name)}</strong><em class="flow-price ${tone(stock.change_pct)}">${number(stock.price)}</em><small>売買代金 ${turnover(stock.turnover)}</small></span>
      <span class="flow-moves"><i class="${tone(stock.change_pct)}">前日 ${percent(stock.change_pct)}</i><i class="${tone(stock.from_open)}">始値 ${percent(stock.from_open)}</i><i class="${tone(stock.vwap_gap)}">VWAP ${percent(stock.vwap_gap)}</i></span>
      <span class="flow-score ${direction === "in" ? "up" : "down"}">${row.score > 0 ? "+" : ""}${number(row.score, 1)}</span>
      <span class="flow-bar"><i style="width:${Math.abs(row.score)}%"></i></span>
    </button>`;
  }).join("");
}

// 直近N分の売買代金増分×値動きで「いま資金が動いている銘柄」を抽出
function flowShortRows(stocks, deltaKey, changeKey) {
  const rows = stocks.map((stock) => {
    const delta = numericValue(stock[deltaKey]);
    const change = numericValue(stock[changeKey]);
    if (delta === null || delta <= 0 || change === null || change === 0) return null;
    return { stock, rawScore: delta * change };
  }).filter(Boolean);
  const maxMagnitude = Math.max(...rows.map((row) => Math.abs(row.rawScore)), 1);
  return rows.map((row) => ({ ...row, score: row.rawScore / maxMagnitude * 100 }));
}

function flowShortRankingHtml(rows, direction, label, deltaKey) {
  const selected = rows
    .filter((row) => direction === "in" ? row.score > 0 : row.score < 0)
    .sort((a, b) => direction === "in" ? b.score - a.score : a.score - b.score)
    .slice(0, 5);
  if (!selected.length) return `<div class="empty-state">該当データがありません（履歴の蓄積後に表示されます）。</div>`;
  return selected.map((row, index) => {
    const stock = row.stock;
    const changeKey = deltaKey === "turnover_delta_5m" ? "change_5m" : "change_15m";
    return `<button class="flow-row" data-analysis-code="${esc(stock.code)}">
      <b>${index + 1}</b>
      <span class="flow-stock"><strong>${esc(stock.code)} ${esc(stock.name)}</strong><em class="flow-price ${tone(stock.change_pct)}">${number(stock.price)}</em><small>${label}Δ代金 ${turnover(stock[deltaKey])}</small></span>
      <span class="flow-moves"><i class="${tone(stock[changeKey])}">${label} ${percent(stock[changeKey])}</i><i class="${tone(stock.vwap_gap)}">VWAP ${percent(stock.vwap_gap)}</i><i>RVOL ${rvolText(stock.rvol)}</i></span>
      <span class="flow-score ${direction === "in" ? "up" : "down"}">${row.score > 0 ? "+" : ""}${number(row.score, 1)}</span>
      <span class="flow-bar"><i style="width:${Math.abs(row.score)}%"></i></span>
    </button>`;
  }).join("");
}

function renderAnalysis() {
  const key = view.snapshot?.updated_at || "";
  if (key === lastAnalysisKey) return;
  lastAnalysisKey = key;
  const stocks = view.snapshot?.stocks || [];
  const rows5 = flowShortRows(stocks, "turnover_delta_5m", "change_5m");
  $("#inflow-5").innerHTML = flowShortRankingHtml(rows5, "in", "5分", "turnover_delta_5m");
  $("#outflow-5").innerHTML = flowShortRankingHtml(rows5, "out", "5分", "turnover_delta_5m");
  const rows15 = flowShortRows(stocks, "turnover_delta_15m", "change_15m");
  $("#inflow-15").innerHTML = flowShortRankingHtml(rows15, "in", "15分", "turnover_delta_15m");
  $("#outflow-15").innerHTML = flowShortRankingHtml(rows15, "out", "15分", "turnover_delta_15m");
  const rows = flowRows(stocks);
  $("#inflow-ranking").innerHTML = flowRankingHtml(rows, "in");
  $("#outflow-ranking").innerHTML = flowRankingHtml(rows, "out");
  document.querySelectorAll("[data-analysis-code]").forEach((button) =>
    button.addEventListener("click", () => {
      view.activeTab = "all";
      selectStock(button.dataset.analysisCode);
      renderTabs();
      renderFilterPanel();
    }));
}

async function toggleFavorite(code) {
  await fetch(`/api/favorites/${encodeURIComponent(code)}/toggle`, { method: "POST" });
  await refresh();
}

async function toggleAlert(code) {
  await fetch(`/api/alerts/${encodeURIComponent(code)}/toggle`, { method: "POST" });
  await refresh();
}

async function selectStock(code) {
  view.selectedCode = code;
  renderAll();
  const stock = view.snapshot.stocks.find((item) => item.code === code);
  if (!stock) return;
  $("#detail-title").textContent = `${stock.code} ${stock.name || ""}`;
  $("#detail-empty").classList.add("hidden");
  $("#detail-content").classList.remove("hidden");
  $("#alert-button").disabled = false;
  const withDate = (pct, dateText) => dateText ? `${percent(pct)} <small>${esc(dateText)}</small>` : percent(pct);
  const withTime = (value, timeText) => `${number(value)} <small>${esc(timeText || "--")}</small>`;
  const stats = [
    ["現在値", `${number(stock.price)} 円`, tone(stock.change_pct)],
    ["前日比", percent(stock.change_pct), tone(stock.change_pct)],
    ["始値比", percent(stock.from_open), tone(stock.from_open)],
    ["VWAP比", percent(stock.vwap_gap), tone(stock.vwap_gap)],
    ["GU/GD", percent(stock.gap_pct), tone(stock.gap_pct)],
    ["RVOL", rvolText(stock.rvol), rvolHot(stock.rvol) ? "up" : ""],
    ["売買代金", turnover(stock.turnover), ""],
    ["15分Δ代金", stock.turnover_delta_15m == null ? "--" : turnover(stock.turnover_delta_15m), tone(stock.change_15m)],
    ["ボラ(日中)", percent(stock.volatility), ""],
    ["業種", esc(stock.sector || "--"), ""],
    ["時価総額", marketCap(stock.market_cap_million), ""],
    ["PER / PBR", `${stock.per ?? "--"} / ${stock.pbr ?? "--"}`, ""],
    ["決算予定", esc(stock.earnings_date || "--"), ""],
    ["始値", withTime(stock.open, stock.open_time), ""],
    ["高値", withTime(stock.high, stock.high_time), "up"],
    ["安値", withTime(stock.low, stock.low_time), "down"],
    ["年高差", withDate(stock.year_high_gap, stock.year_high_date), ""],
    ["上場高差", withDate(stock.listing_high_gap, stock.listing_high_date), ""],
    ["年安差", withDate(stock.year_low_gap, stock.year_low_date), ""],
    ["上場安差", withDate(stock.listing_low_gap, stock.listing_low_date), ""],
  ];
  $("#detail-stats").innerHTML = stats.map(([label, value, css]) =>
    `<div><span>${label}</span><strong class="${css}">${value}</strong></div>`).join("");
  await renderChart(code);
}

async function renderChart(code) {
  const history = await (await fetch(`/api/history/${encodeURIComponent(code)}?minutes=180`)).json();
  const prices = history
    .map((item) => numericValue(item.price))
    .filter((price) => price !== null && price > 0);
  if (prices.length < 2) {
    $("#price-chart").innerHTML = `<div class="empty-state">履歴を蓄積中です。</div>`;
    return;
  }
  const min = Math.min(...prices), max = Math.max(...prices), span = max - min || 1;
  const width = 560, height = 170;
  const coords = prices.map((price, index) => `${(index / (prices.length - 1) * width).toFixed(1)},${(height - ((price - min) / span) * (height - 20) - 10).toFixed(1)}`).join(" ");
  $("#price-chart").innerHTML = `<div class="chart-labels"><span>${number(max)}</span><span>${number(min)}</span></div>
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><polyline points="${coords}" class="${prices.at(-1) >= prices[0] ? "chart-up" : "chart-down"}"></polyline></svg>`;
}

// #3: esc() applied to alert fields from DB
function renderAlerts(alerts) {
  const key = alerts.map((a) => `${a.id}:${a.acknowledged ? 1 : 0}`).join(",");
  if (key === lastAlertKey) return;
  lastAlertKey = key;
  $("#alert-list").innerHTML = alerts.length ? alerts.map((alert) =>
    `<article class="alert-item ${alert.acknowledged ? "" : "unread"}"><div><strong>${esc(alert.code)} ${esc(alert.name)}</strong><time>${esc(alert.occurred_at)}</time></div><p>${esc(alert.message)}</p></article>`
  ).join("") : `<div class="empty-state">まだアラートはありません。</div>`;
}

function notifyNewAlerts(alerts) {
  if (view.firstLoad) {
    alerts.forEach((alert) => view.seenAlertIds.add(alert.id));
    view.firstLoad = false;
    return;
  }
  alerts.forEach((alert) => {
    if (view.seenAlertIds.has(alert.id)) return;
    view.seenAlertIds.add(alert.id);
    if (Notification.permission === "granted") new Notification(`${alert.code} ${alert.name || ""}`, { body: alert.message });
  });
  if (view.seenAlertIds.size > 500) {
    const excess = [...view.seenAlertIds].slice(0, view.seenAlertIds.size - 200);
    excess.forEach((id) => view.seenAlertIds.delete(id));
  }
}

function renderAlertFields(rule) {
  $("#alert-fields").innerHTML = CONDITIONS.filter((condition) => !condition.filterOnly).map((condition) => `
    <label class="rule-row">
      <input type="checkbox" name="${condition.id}_on" ${rule[`${condition.id}_on`] !== 0 ? "checked" : ""}>
      <span>${condition.label}</span>
      <div class="input-with-unit"><input name="${condition.id}" type="number" step="0.1" value="${rule[condition.id] ?? ""}"><i>${condition.unit}</i></div>
    </label>
  `).join("");
}

async function openAlertDialog() {
  const stock = view.snapshot?.stocks.find((item) => item.code === view.selectedCode);
  if (!stock) return;
  const existing = view.snapshot.rules?.[stock.code];
  const rule = existing || await (await fetch(`/api/alerts/${encodeURIComponent(stock.code)}/defaults`)).json();
  $("#alert-dialog-title").textContent = `${stock.code} ${stock.name || ""}`;
  $("#alert-form").elements.enabled.checked = rule.enabled !== 0;
  renderAlertFields(rule);
  $("#alert-dialog").showModal();
}

let _refreshing = false;
async function refresh() {
  if (_refreshing) return;
  _refreshing = true;
  try {
    view.snapshot = await (await fetch("/api/snapshot", { cache: "no-store" })).json();
    const healthy = !view.snapshot.error;
    $("#status-dot").classList.toggle("online", healthy);
    $("#status-text").textContent = healthy ? "Excel接続中" : "接続エラー";
    $("#updated-at").textContent = view.snapshot.updated_at ? `更新 ${view.snapshot.updated_at}` : "--";
    const sess = view.snapshot.market_session || "";
    const badge = $("#session-badge");
    badge.textContent = view.snapshot.session_mismatch ? `${sess} 要確認` : sess;
    badge.classList.toggle("pts", sess === "PTS");
    badge.classList.toggle("mismatch", Boolean(view.snapshot.session_mismatch));
    badge.title = view.snapshot.session_mismatch
      ? `Excel実状態: ${view.snapshot.detected_session} / 時刻上の想定: ${view.snapshot.expected_session}`
      : `Excel実状態: ${view.snapshot.detected_session || "未検出"}`;
    $("#error-banner").textContent = view.snapshot.error || "";
    $("#error-banner").classList.toggle("hidden", healthy);
    renderTabs();
    renderFilterPanel();
    renderAll();
    renderAlerts(view.snapshot.alerts);
    notifyNewAlerts(view.snapshot.alerts);
  } catch {
    $("#status-text").textContent = "サーバー切断";
    $("#status-dot").classList.remove("online");
  } finally {
    _refreshing = false;
  }
}

$("#alert-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = { enabled: form.elements.enabled.checked };
  CONDITIONS.filter((condition) => !condition.filterOnly).forEach((condition) => {
    payload[condition.id] = form.elements[condition.id].value || null;
    payload[`${condition.id}_on`] = form.elements[`${condition.id}_on`].checked;
  });
  await fetch(`/api/alerts/${encodeURIComponent(view.selectedCode)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  $("#alert-dialog").close();
  await refresh();
});

$("#search").addEventListener("input", renderAll);
$("#sort").addEventListener("change", renderAll);
document.querySelectorAll('input[name="sort-direction"]').forEach((input) =>
  input.addEventListener("change", renderAll));
$("#only-alerts").addEventListener("change", renderAll);
$("#alert-button").addEventListener("click", openAlertDialog);
$("#dialog-close").addEventListener("click", () => $("#alert-dialog").close());
$("#dialog-cancel").addEventListener("click", () => $("#alert-dialog").close());
$("#filter-toggle").addEventListener("click", () => {
  $("#filter-body").classList.toggle("hidden");
  renderFilterPanel();
});
$("#filter-value").addEventListener("input", () => {
  const condition = activeCondition();
  if (!condition) return;
  view.filterValues[condition.id] = Number($("#filter-value").value);
  renderFilterPanel();
  renderAll();
});
$("#filter-reset").addEventListener("click", () => {
  const condition = activeCondition();
  if (!condition) return;
  delete view.filterValues[condition.id];
  renderFilterPanel();
  renderAll();
});
$("#notify-button").addEventListener("click", async () => {
  const permission = await Notification.requestPermission();
  $("#notify-button").textContent = permission === "granted" ? "通知は有効です" : "通知は許可されていません";
});
$("#ack-button").addEventListener("click", async () => {
  await fetch("/api/alerts/acknowledge", { method: "POST" });
  await refresh();
});

refresh();
setInterval(refresh, 2000);
