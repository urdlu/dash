/**
 * WS Transactions sync — Google Apps Script port of legacy/ws-sync/*.py
 *
 * Runs INSIDE the wstradesws@gmail.com Google account on a time-driven
 * trigger, so there are no OAuth refresh tokens to expire, no laptop, and no
 * Claude/LLM tokens spent per run.
 *
 * What it does (same as ws_sync.py):
 *   1. Lists recent emails in this inbox (Wealthsimple confirmations forwarded
 *      from Tim's main address).
 *   2. Parses each new one with the exact rules from ws_parser.py.
 *   3. Appends transaction rows to the "Untitled" tab of "WS Transactions Log".
 *   4. Remembers processed Gmail message IDs (hidden "_ws_sync_state" tab) so
 *      re-runs never double-log.
 *
 * Setup: see README.md in this folder. Run `setup()` once from the editor.
 *
 * The parser section below is pure JS (no Apps Script services) so it can be
 * parity-tested in Node against the original Python — see test/.
 */

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
var SPREADSHEET_ID = '15JYrY5pq1D9Xe6wbL-f3G5GOn_0ggNeaqR1uCBic8Kw';
var LOG_SHEET = 'Untitled';
var STATE_SHEET = '_ws_sync_state';
var MAX_MESSAGES = 100;

// ===========================================================================
// PARSER — faithful port of ws_parser.py (keep rules identical)
// ===========================================================================

var ALL_LABELS = [
  'Account', 'Account nickname', 'Type', 'Option', 'Symbol', 'Contracts',
  'Shares', 'Expiry', 'Average price', 'Total value', 'Total cost', 'Time',
  'Amount', 'Amount submitted', 'Amount received', 'From', 'To', 'Status',
];

function reEscape_(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function clean_(text) {
  return text
    .split('͏').join('')
    .split('‌').join('')
    .split('​').join('')
    .split('�').join("'")
    .split('*').join('');
}

function lines_(text) {
  return clean_(text).split('\n').map(function (l) { return l.trim(); })
    .filter(function (l) { return l.length > 0; });
}

function field_(lines, label) {
  for (var i = 0; i < lines.length; i++) {
    var l = lines[i];
    if (l.indexOf(label + ':') === 0) {
      var val = l.slice(l.indexOf(':') + 1).trim();
      if (val) return val;
    }
  }
  var joined = lines.join('\n');
  var others = ALL_LABELS.filter(function (x) { return x !== label; }).map(reEscape_).join('|');
  var re = new RegExp(reEscape_(label) + ':\\s*([\\s\\S]*?)(?:' + others + ':|$)');
  var m = re.exec(joined);
  if (m) {
    var v = m[1].replace(/\s+/g, ' ').trim();
    return v || null;
  }
  return null;
}

function money_(s) {
  if (!s) return [null, null];
  var m = /(US)?\$?\s*([\d,]+\.?\d*)\s*(USD|CAD)?/.exec(s);
  if (!m) return [null, null];
  var amount = parseFloat(m[2].split(',').join(''));
  var currency = m[3] || (m[1] ? 'USD' : 'CAD');
  return [currency, amount];
}

var MONTH_MAP = {
  January: 1, February: 2, March: 3, April: 4, May: 5, June: 6,
  July: 7, August: 8, September: 9, October: 10, November: 11, December: 12,
};

function pad2_(n) { return (n < 10 ? '0' : '') + n; }

function parse(text) {
  var lines = lines_(text);
  var joined = lines.join('\n');

  var subject = (lines.length && lines[0].indexOf('Subject:') === 0)
    ? lines[0].split('Subject:').join('').trim() : '';
  var dateLine = '';
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].indexOf('Date:') === 0) { dateLine = lines[i]; break; }
  }

  if (joined.indexOf('Options strategy:') !== -1) return parseMultiLegFill_(lines, dateLine);
  if (joined.indexOf('Your order has been filled') !== -1 ||
      joined.indexOf('Your order has been partially filled') !== -1) return parseOrderFill_(lines, dateLine);
  if (joined.indexOf('Your order has been cancelled') !== -1) return parseOrderCancelled_(lines, dateLine);
  if (joined.indexOf('Your options have been exercised or assigned') !== -1) return parseExerciseAssignment_(lines, dateLine);
  if (joined.indexOf('You earned a dividend') !== -1) return parseDividend_(lines, dateLine);
  if (joined.indexOf('You made a transfer') !== -1) return parseTransfer_(lines, dateLine);
  if (joined.indexOf('Your funds have been converted') !== -1) return parseFx_(lines, dateLine);
  if (joined.indexOf('We got your direct deposit') !== -1 || subject.indexOf('Direct deposit received') !== -1) {
    return parseDirectDeposit_(lines, dateLine);
  }
  if (subject.indexOf('Wire transfer received') !== -1 ||
      /^Amount:.*\nFrom:.*\nTo:.*\nStatus:/m.test(joined)) {
    if (joined.indexOf('Wire transfer received') !== -1) return parseWireTransfer_(lines, dateLine);
  }

  var ignoredMarkers = [
    'credit card payment', 'credit card statement', 'fee schedule',
    'account agreement', 'crypto trading fees', 'options expiring',
    'until your options expire',
  ];
  var jl = joined.toLowerCase(), sl = subject.toLowerCase();
  for (var k = 0; k < ignoredMarkers.length; k++) {
    if (jl.indexOf(ignoredMarkers[k]) !== -1 || sl.indexOf(ignoredMarkers[k]) !== -1) {
      return { transaction_type: 'ignored', subject: subject, date: dateLine };
    }
  }
  return { transaction_type: 'unrecognized', subject: subject, date: dateLine };
}

// Python's strptime("%B %d, %Y") — month name is case-insensitive there.
function parseLongDate_(s) {
  var m = /^([A-Za-z]+) (\d{1,2}), (\d{4})$/.exec(s);
  if (!m) return null;
  var month = null;
  for (var name in MONTH_MAP) {
    if (name.toLowerCase() === m[1].toLowerCase()) month = MONTH_MAP[name];
  }
  if (!month) return null;
  var y = +m[3], d = +m[2];
  var dt = new Date(Date.UTC(y, month - 1, d));
  if (dt.getUTCMonth() !== month - 1) return null;
  return { year: y, month: month, day: d };
}

function inferExpiryIso_(monthName, day, eff) {
  var month = MONTH_MAP[monthName];
  if (!month || !eff) return monthName + ' ' + day;
  var year = eff.year;
  if (month < eff.month) year += 1;
  return year + '-' + pad2_(month) + '-' + pad2_(parseInt(day, 10));
}

function parseMultiLegFill_(lines, dateLine) {
  var joined = lines.join('\n');
  var account = field_(lines, 'Account');
  var orderType = field_(lines, 'Type');
  var sm = /Options strategy:\s*([A-Za-z ]+?)(?:\n|Contracts:|$)/.exec(joined);
  var strategy = sm ? sm[1].trim() : null;
  var timeRaw = field_(lines, 'Time');

  var eff = null;
  var tm = /([A-Za-z]+ \d{1,2}, \d{4})/.exec(timeRaw || '');
  if (tm) eff = parseLongDate_(tm[1]);

  var legRe = /(Sell|Buy) to (open|close) (\d+) ([A-Z]+) ([A-Za-z]+) (\d{1,2}) \$?([\d.]+) (Call|Put)/g;
  var legs = [], lm;
  while ((lm = legRe.exec(joined)) !== null) {
    legs.push({
      action: lm[1].toUpperCase(),
      open_close: lm[2].toUpperCase(),
      quantity: parseInt(lm[3], 10),
      underlying: lm[4],
      expiry: inferExpiryIso_(lm[5], lm[6], eff),
      strike: parseFloat(lm[7]),
      option_type: lm[8].toLowerCase(),
    });
  }

  var costRaw = field_(lines, 'Total cost');
  var premiumRaw = field_(lines, 'Total premium');
  var currency = null, netCash = null, r;
  if (costRaw) {
    r = money_(costRaw); currency = r[0];
    netCash = r[1] !== null ? -r[1] : null;
  } else if (premiumRaw) {
    r = money_(premiumRaw); currency = r[0]; netCash = r[1];
  }

  var set = {};
  legs.forEach(function (l) { set[l.underlying] = true; });
  var unds = Object.keys(set).sort();

  return {
    transaction_type: 'multi_leg_option_fill',
    account: account, order_type: orderType, strategy: strategy, legs: legs,
    underlying: unds.length === 1 ? unds[0] : unds.join('/'),
    net_cash: netCash, net_cash_currency: currency,
    time: timeRaw, date: dateLine,
  };
}

function parseOrderFill_(lines, dateLine) {
  var account = field_(lines, 'Account');
  var orderType = field_(lines, 'Type');
  var optionDesc = field_(lines, 'Option');
  var symbol = field_(lines, 'Symbol');
  var contracts = field_(lines, 'Contracts');
  var shares = field_(lines, 'Shares');
  var expiry = field_(lines, 'Expiry');
  var avgRaw = field_(lines, 'Average price');
  var totalRaw = field_(lines, 'Total value') || field_(lines, 'Total cost');
  var timeRaw = field_(lines, 'Time');
  var partial = lines.some(function (l) { return l.indexOf('partially filled') !== -1; });

  var avg = money_(avgRaw);
  var total = money_(totalRaw);
  var isOption = optionDesc !== null;
  var ot = orderType ? orderType.toLowerCase() : '';
  var action = orderType && ot.indexOf('sell') !== -1 ? 'SELL' : (orderType && ot.indexOf('buy') !== -1 ? 'BUY' : null);
  var openClose = orderType && ot.indexOf('open') !== -1 ? 'OPEN' : (orderType && ot.indexOf('close') !== -1 ? 'CLOSE' : null);

  var result = {
    transaction_type: isOption ? 'option_fill' : 'stock_fill',
    partial: partial, account: account, action: action, order_type: orderType,
    avg_price: avg[1], avg_price_currency: avg[0], total_value: total[1],
    time: timeRaw, date: dateLine,
  };

  if (isOption) {
    var om = /^([A-Z]+)\s+([\d.]+)\s+(call|put)/i.exec(optionDesc);
    result.underlying = om ? om[1] : null;
    result.strike = om ? parseFloat(om[2]) : null;
    result.option_type = om ? om[3].toLowerCase() : null;
    result.open_close = openClose;
    result.contracts = (contracts && /^\d+$/.test(contracts.trim())) ? parseInt(contracts.trim(), 10) : contracts;
    result.expiry = expiry;
    result.raw_option_desc = optionDesc;
  } else {
    var filled = shares, totalShares = shares;
    if (shares && shares.indexOf(' of ') !== -1) {
      var parts = shares.split(' of ');
      filled = parts[0].trim(); totalShares = parts[1].trim();
    }
    result.symbol = symbol;
    result.shares_filled = filled ? parseFloat(filled) : null;
    result.shares_ordered = totalShares ? parseFloat(totalShares) : null;
  }
  return result;
}

function parseOrderCancelled_(lines, dateLine) {
  return {
    transaction_type: 'order_cancelled',
    account: field_(lines, 'Account'), order_type: field_(lines, 'Type'),
    raw_option_desc: field_(lines, 'Option'), symbol: field_(lines, 'Symbol'),
    date: dateLine,
  };
}

function parseExerciseAssignment_(lines, dateLine) {
  var joined = lines.join('\n');
  var dm = /exercised and assigned on ([A-Za-z]+ \d+, \d{4})/i.exec(joined);
  var om = /([A-Z]+)\s+\$?([\d.]+)\s+(Put|Call)/.exec(joined);
  var cm = /(-?\d+)\s+contracts/.exec(joined);
  var outm = /(Assigned|Exercised)/.exec(joined);
  var am = /(Bought|Sold)\s+([\d.]+)\s+shares/.exec(joined);
  var tm = /Total Cost:\s*\$?([\d,]+\.?\d*)\s*(USD|CAD)?/.exec(joined);
  return {
    transaction_type: 'option_exercise_assignment',
    account: field_(lines, 'Account'),
    event_date: dm ? dm[1] : null,
    underlying: om ? om[1] : null,
    strike: om ? parseFloat(om[2]) : null,
    option_type: om ? om[3].toLowerCase() : null,
    contracts: cm ? parseInt(cm[1], 10) : null,
    outcome: outm ? outm[1] : null,
    share_action: am ? am[1].toUpperCase() : null,
    shares: am ? parseFloat(am[2]) : null,
    total_cost: tm ? parseFloat(tm[1].split(',').join('')) : null,
    total_cost_currency: (tm && tm[2]) ? tm[2] : null,
    date: dateLine,
  };
}

function parseDividend_(lines, dateLine) {
  return {
    transaction_type: 'dividend', account: field_(lines, 'Account'),
    symbol: field_(lines, 'Symbol'), amount: money_(field_(lines, 'Amount'))[1],
    date: dateLine,
  };
}

function parseTransfer_(lines, dateLine) {
  var m = money_(field_(lines, 'Amount'));
  return {
    transaction_type: 'internal_transfer', amount: m[1], currency: m[0],
    from_account: field_(lines, 'From'), to_account: field_(lines, 'To'),
    date: dateLine,
  };
}

function parseFx_(lines, dateLine) {
  var s = money_(field_(lines, 'Amount submitted'));
  var r = money_(field_(lines, 'Amount received'));
  return {
    transaction_type: 'fx_conversion', account: field_(lines, 'Account'),
    amount_submitted: s[1], submitted_currency: s[0],
    amount_received: r[1], received_currency: r[0], date: dateLine,
  };
}

function parseDirectDeposit_(lines, dateLine) {
  return {
    transaction_type: 'direct_deposit',
    amount: money_(field_(lines, 'Amount'))[1],
    from_source: field_(lines, 'From'), date: dateLine,
  };
}

function parseWireTransfer_(lines, dateLine) {
  var m = money_(field_(lines, 'Amount'));
  return {
    transaction_type: 'wire_transfer', amount: m[1], currency: m[0],
    from_source: field_(lines, 'From'), to_account: field_(lines, 'To'),
    status: field_(lines, 'Status'), date: dateLine,
  };
}

// ===========================================================================
// ROW BUILDING — faithful port of ws_sync._to_row / _parse_dt
// ===========================================================================

// Python str() of a value inside an f-string.
function py_(v) {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  return String(v);
}
// Python str() of a float (474.0 -> "474.0").
function pyF_(v) {
  if (v === null || v === undefined) return 'None';
  return Number.isInteger(v) ? v + '.0' : String(v);
}
// Python format(x, 'g').
function pyG_(v) {
  return String(Number(Number(v).toPrecision(6)));
}

var DAYS_ = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
var MONS_ = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

// Returns "YYYY-MM-DD HH:MM" or "" (Python: datetime.min -> "").
function rowDate_(record) {
  var m = /[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4} [\d:]+/.exec(record.date || '');
  if (!m) return '';
  var p = /^([A-Za-z]{3}), (\d{1,2}) ([A-Za-z]{3}) (\d{4}) (\d{1,2}):(\d{1,2}):(\d{1,2})$/.exec(m[0]);
  if (!p) return '';
  if (DAYS_.indexOf(p[1].toLowerCase()) === -1) return '';
  var mon = MONS_.indexOf(p[3].toLowerCase());
  if (mon === -1) return '';
  var y = +p[4], d = +p[2], H = +p[5], M = +p[6], S = +p[7];
  if (H > 23 || M > 59 || S > 61) return '';
  var dt = new Date(Date.UTC(y, mon, d));
  if (dt.getUTCMonth() !== mon || d < 1) return '';
  return y + '-' + pad2_(mon + 1) + '-' + pad2_(d) + ' ' + pad2_(H) + ':' + pad2_(M);
}

/** Returns [date, type, account, holdingsImpact, detail, amount] or null. */
function toRow(r) {
  var t = r.transaction_type;
  var holdings = ['stock_fill', 'option_fill', 'option_exercise_assignment', 'multi_leg_option_fill'].indexOf(t) !== -1;
  var detail, amount;

  if (t === 'multi_leg_option_fill') {
    var legStrs = (r.legs || []).map(function (l) {
      return l.action + ' to ' + l.open_close + ' ' + l.quantity + 'x ' + l.underlying +
        ' $' + pyG_(l.strike) + ' ' + l.option_type + ' exp ' + l.expiry;
    });
    detail = py_(r.strategy) + ': ' + legStrs.join('; ');
    amount = r.net_cash;
  } else if (t === 'stock_fill') {
    detail = py_(r.action) + ' ' + pyF_(r.shares_filled) + ' ' + py_(r.symbol) +
      ' @ ' + pyF_(r.avg_price) + ' ' + py_(r.avg_price_currency);
    if (r.partial) detail += ' (PARTIAL)';
    amount = r.total_value;
  } else if (t === 'option_fill') {
    detail = py_(r.action) + ' to ' + py_(r.open_close) + ' ' + py_(r.contracts) + 'x ' +
      py_(r.raw_option_desc) + ' exp ' + py_(r.expiry) +
      ' @ ' + pyF_(r.avg_price) + ' ' + py_(r.avg_price_currency);
    amount = r.total_value;
  } else if (t === 'option_exercise_assignment') {
    detail = py_(r.outcome) + ': ' + py_(r.share_action) + ' ' + pyF_(r.shares) + ' ' +
      py_(r.underlying) + ' (from ' + py_(r.contracts) + 'x $' + pyF_(r.strike) + ' ' +
      py_(r.option_type) + ')';
    amount = r.total_cost;
  } else if (t === 'order_cancelled') {
    detail = 'Cancelled: ' + py_(r.order_type) + ' ' + py_(r.raw_option_desc || r.symbol);
    amount = null;
  } else if (t === 'dividend') {
    detail = 'Dividend from ' + py_(r.symbol);
    amount = r.amount;
  } else if (t === 'internal_transfer') {
    detail = 'Transfer ' + py_(r.from_account) + ' -> ' + py_(r.to_account);
    amount = r.amount;
  } else if (t === 'fx_conversion') {
    detail = 'FX: ' + pyF_(r.amount_submitted) + ' ' + py_(r.submitted_currency) +
      ' -> ' + pyF_(r.amount_received) + ' ' + py_(r.received_currency);
    amount = r.amount_received;
  } else if (t === 'direct_deposit') {
    detail = 'Deposit from ' + py_(r.from_source);
    amount = r.amount;
  } else if (t === 'wire_transfer') {
    detail = 'Wire from ' + py_(r.from_source) + ' -> ' + py_(r.to_account);
    amount = r.amount;
  } else {
    return null;
  }

  return [
    rowDate_(r), t, r.account || r.to_account || '', holdings ? 'Y' : '',
    detail, amount === undefined ? null : amount,
  ];
}

// ===========================================================================
// EMAIL BODY — port of gmail_client.extract_body
// ===========================================================================

function looksLikeHtml_(s) {
  var head = s.replace(/^\s+/, '').slice(0, 200).toLowerCase();
  return head.indexOf('<!doctype') === 0 || head.indexOf('<html') !== -1;
}

function htmlToText(h) {
  var t = h.replace(/<(style|script)\b[\s\S]*?<\/\1>/gi, ' ');
  t = t.replace(/<br\s*\/?>/gi, '\n');
  t = t.replace(/<\/(p|div|tr|td|th|li|h[1-6])>/gi, '\n');
  t = t.replace(/<[^>]+>/g, ' ');
  t = t.replace(/[ \t]+/g, ' ');
  t = t.replace(/\n\s*\n+/g, '\n');
  return t.split('\n').map(function (l) { return l.trim(); })
    .filter(function (l) { return l; }).join('\n');
}

/** decodePart is injected so this is testable outside Apps Script. */
function extractBody(msg, decodePart) {
  var plain = null, html = null;
  function walk(part) {
    var mime = part.mimeType || '';
    var body = part.body || {};
    if (mime === 'text/plain' && body.data && plain === null) plain = decodePart(body.data);
    else if (mime === 'text/html' && body.data && html === null) html = decodePart(body.data);
    (part.parts || []).forEach(walk);
  }
  walk(msg.payload || {});
  if (plain && plain.trim().length > 20 && looksLikeHtml_(plain)) plain = null;
  if (plain && plain.trim().length > 20) return plain;
  if (html) return htmlToText(html);
  return '';
}

function header_(msg, name) {
  var hs = (msg.payload && msg.payload.headers) || [];
  for (var i = 0; i < hs.length; i++) {
    if (hs[i].name.toLowerCase() === name.toLowerCase()) return hs[i].value;
  }
  return '';
}

// ===========================================================================
// APPS SCRIPT RUNTIME — everything below uses Google services
// ===========================================================================

function decodeGmailData_(data) {
  // The Advanced Gmail service may hand back body.data as a byte array or as
  // a base64url string depending on runtime; handle both.
  if (Array.isArray(data)) return Utilities.newBlob(data).getDataAsString('UTF-8');
  var s = String(data);
  s += '===='.slice(0, (4 - s.length % 4) % 4);
  return Utilities.newBlob(Utilities.base64DecodeWebSafe(s)).getDataAsString('UTF-8');
}

function stateSheet_(ss) {
  var sh = ss.getSheetByName(STATE_SHEET);
  if (!sh) {
    sh = ss.insertSheet(STATE_SHEET);
    sh.getRange('A1:B1').setValues([['processed_message_id', 'last_checked_date']]);
    sh.hideSheet();
  }
  return sh;
}

function loadState_(sh) {
  var last = sh.getLastRow();
  var ids = last > 1 ? sh.getRange(2, 1, last - 1, 1).getValues().map(function (r) { return String(r[0]); }).filter(String) : [];
  var lastDate = String(sh.getRange('B2').getValue() || '') || null;
  return { processed: ids, lastCheckedDate: lastDate };
}

function existingRowKeys_(logSheet, tz) {
  var keys = {};
  var n = logSheet.getLastRow();
  if (n < 1) return keys;
  logSheet.getRange(1, 1, n, 5).getValues().forEach(function (r) {
    var d = r[0] instanceof Date ? Utilities.formatDate(r[0], tz, 'yyyy-MM-dd HH:mm') : String(r[0]);
    keys[d + '|' + r[1] + '|' + r[4]] = true;
  });
  return keys;
}

/** Main entry point — the time-driven trigger calls this. */
function syncWsTransactions() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  try {
    run_(false);
  } catch (e) {
    notify_('WS sync FAILED', String(e && e.stack || e), 'warning');
    throw e;
  } finally {
    lock.releaseLock();
  }
}

/** Run from the editor to see what WOULD be logged, without writing anything. */
function dryRun() { return run_(true); }

function run_(dryRun) {
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var logSheet = ss.getSheetByName(LOG_SHEET);
  if (!logSheet) throw new Error('Tab "' + LOG_SHEET + '" not found in WS Transactions Log');
  var stSheet = stateSheet_(ss);
  var state = loadState_(stSheet);
  var processed = {};
  state.processed.forEach(function (id) { processed[id] = true; });

  // First run (no saved state): the old laptop pipeline's sync_state.json
  // isn't available, so de-duplicate against rows already in the log instead.
  var seedRun = state.processed.length === 0;
  var seen = seedRun ? existingRowKeys_(logSheet, ss.getSpreadsheetTimeZone()) : {};

  var q = state.lastCheckedDate ? 'after:' + state.lastCheckedDate : '';
  var resp = Gmail.Users.Messages.list('me', { q: q, maxResults: MAX_MESSAGES });
  var messages = (resp && resp.messages) || [];

  var newRows = [], newIds = [], ignored = 0, dupes = 0, maxInternal = 0;
  messages.forEach(function (m) {
    if (processed[m.id]) return;
    var full = Gmail.Users.Messages.get('me', m.id, { format: 'full' });
    var body = extractBody(full, decodeGmailData_);
    var text = 'Subject: ' + header_(full, 'Subject') + '\nDate: ' + header_(full, 'Date') + '\n\n' + body;
    var row = toRow(parse(text));
    newIds.push(m.id);
    maxInternal = Math.max(maxInternal, Number(full.internalDate || 0));
    if (!row) { ignored++; return; }
    var key = row[0] + '|' + row[1] + '|' + row[4];
    if (seen[key]) { dupes++; return; }
    seen[key] = true;
    newRows.push(row.map(function (v) { return v === null ? '' : v; }));
  });

  // Oldest first, like they'd have arrived.
  newRows.reverse();

  if (!dryRun) {
    if (newRows.length) {
      logSheet.getRange(logSheet.getLastRow() + 1, 1, newRows.length, 6).setValues(newRows);
    }
    if (newIds.length) {
      stSheet.getRange(stSheet.getLastRow() + 1, 1, newIds.length, 1)
        .setValues(newIds.map(function (id) { return [id]; }));
    }
    if (maxInternal) {
      // Re-scan from the day before the newest email: Gmail's after: is
      // date-granular and timezone-fuzzy, and processed IDs prevent dupes.
      var d = new Date(maxInternal - 24 * 3600 * 1000);
      stSheet.getRange('B2').setValue(Utilities.formatDate(d, 'America/Vancouver', 'yyyy/MM/dd'));
    }
    if (newRows.length) {
      notify_(newRows.length + ' new Wealthsimple transaction' + (newRows.length > 1 ? 's' : ''),
        newRows.map(function (r) { return r[4]; }).join('\n'), 'chart_with_upwards_trend');
    }
  }

  var summary = 'Checked ' + messages.length + ' message(s), ' + newIds.length + ' new, ' +
    newRows.length + ' logged, ' + ignored + ' ignored/non-transactional' +
    (seedRun ? ', ' + dupes + ' already in log (seed run)' : '') + (dryRun ? ' [DRY RUN]' : '') + '.';
  Logger.log(summary);
  if (dryRun) newRows.forEach(function (r) { Logger.log(JSON.stringify(r)); });
  return summary;
}

/** Optional phone push via ntfy.sh. Set Script Property NTFY_TOPIC to enable. */
function notify_(title, message, tags) {
  var topic = PropertiesService.getScriptProperties().getProperty('NTFY_TOPIC');
  if (!topic) return;
  try {
    UrlFetchApp.fetch('https://ntfy.sh/' + encodeURIComponent(topic), {
      method: 'post', payload: String(message).slice(0, 3000),
      headers: { Title: title, Tags: tags || '' }, muteHttpExceptions: true,
    });
  } catch (e) { Logger.log('ntfy failed: ' + e); }
}

/** Run once: authorizes, does a first sync, and installs an hourly trigger. */
function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'syncWsTransactions') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('syncWsTransactions').timeBased().everyHours(1).create();
  syncWsTransactions();
}

// Node test hook (ignored by Apps Script, where `module` is undefined).
if (typeof module !== 'undefined') {
  module.exports = { parse: parse, toRow: toRow, extractBody: extractBody, htmlToText: htmlToText };
}
