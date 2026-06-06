'use strict';

var cs     = window.__adobe_cep__;
var BASE   = null;  // set from config.json via Node.js
var _fs    = null;
var _path  = null;

// ── Node.js bootstrap (safe — panel opens even if this fails) ─────────────

(function () {
    try {
        _path = require('path');
        _fs   = require('fs');
        var cfg = JSON.parse(_fs.readFileSync(_path.join(__dirname, '..', 'config.json'), 'utf8'));
        BASE = cfg.basePath;

        function bg(cmd, args) {
            var p = require('child_process').spawn(cmd, args,
                { cwd: BASE, detached: true, stdio: 'ignore', windowsHide: true,
                  creationFlags: 0x08000000 }); // CREATE_NO_WINDOW
            p.unref();
        }
        bg('pythonw', [_path.join(BASE, 'server.py')]);
        bg('pythonw', [_path.join(BASE, 'tracker.py')]);
        bg('pythonw', [_path.join(BASE, 'tray.py')]);
    } catch (e) {
        console.log('[PremLogger] Node bootstrap failed:', e.message);
    }
}());

// ── data reading ──────────────────────────────────────────────────────────

var TICK_SEC    = 10;
var SESSION_GAP = 600;

function dateStr(daysAgo) {
    var d = new Date(); d.setDate(d.getDate() - (daysAgo || 0));
    return d.toISOString().slice(0, 10);
}

function readDay(ds) {
    if (!_fs || !BASE) return [];
    var f = _path.join(BASE, 'data', ds + '.jsonl');
    if (!_fs.existsSync(f)) return [];
    return _fs.readFileSync(f, 'utf8').split('\n').filter(Boolean).map(function (l) {
        try { return JSON.parse(l); } catch (e) { return null; }
    }).filter(Boolean);
}

function readRange(days) {
    var out = [];
    for (var i = days - 1; i >= 0; i--) {
        readDay(dateStr(i)).forEach(function (t) { out.push(t); });
    }
    return out;
}

function fmt(s) {
    s = Math.round(s);
    if (s < 60)   return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return m ? h + 'h ' + m + 'm' : h + 'h';
}

function fmtTime(ts) {
    var d = new Date(ts.replace(' ', 'T'));
    var h = d.getHours(), mi = d.getMinutes();
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return h + ':' + (mi < 10 ? '0' : '') + mi + ' ' + ampm;
}

function projectColor(name) {
    var hash = 0;
    for (var i = 0; i < name.length; i++) hash = Math.imul(31, hash) + name.charCodeAt(i) | 0;
    var hue = ((hash % 360) + 360) % 360;
    return 'hsl(' + hue + ',55%,52%)';
}

// ── aggregation ───────────────────────────────────────────────────────────

function byProjectAndSeq(ticks) {
    var projects = {}, order = [];
    ticks.filter(function (t) { return t.app === 'Adobe Premiere'; }).forEach(function (t) {
        var p = t.project || '(unknown)', s = t.seq || '(no sequence)';
        if (!projects[p]) { projects[p] = { active: 0, idle: 0, seqs: {}, seqOrder: [] }; order.push(p); }
        projects[p][t.status || 'active'] += TICK_SEC;
        if (!projects[p].seqs[s]) { projects[p].seqs[s] = { active: 0, idle: 0 }; projects[p].seqOrder.push(s); }
        projects[p].seqs[s][t.status || 'active'] += TICK_SEC;
    });
    return order.map(function (name) {
        return { name: name, active: projects[name].active, idle: projects[name].idle,
                 seqs: projects[name].seqOrder.map(function (sn) {
                     return { name: sn, active: projects[name].seqs[sn].active,
                              idle: projects[name].seqs[sn].idle };
                 }).sort(function (a, b) { return b.active - a.active; }) };
    }).sort(function (a, b) { return b.active - a.active; });
}

function toSessions(ticks) {
    var sessions = [], cur = null;
    ticks.filter(function (t) { return t.app === 'Adobe Premiere'; }).forEach(function (t) {
        var ts = new Date(t.ts.replace(' ', 'T'));
        if (!cur || (ts - cur._ts) / 1000 > SESSION_GAP) {
            if (cur) { cur.end = cur._endStr; cur.duration = cur.active + cur.idle; delete cur._ts; delete cur._endStr; sessions.push(cur); }
            cur = { project: t.project, sequence: t.seq || null, start: t.ts, active: 0, idle: 0, _ts: ts, _endStr: t.ts };
        }
        cur[t.status || 'active'] += TICK_SEC;
        cur._ts = ts; cur._endStr = t.ts;
    });
    if (cur) { cur.end = cur._endStr; cur.duration = cur.active + cur.idle; delete cur._ts; delete cur._endStr; sessions.push(cur); }
    return sessions.reverse();
}

// ── render ────────────────────────────────────────────────────────────────

function renderProjects(containerId, projs, showBars) {
    var el = document.getElementById(containerId);
    if (!projs.length) { el.innerHTML = '<div class="empty">No data yet.</div>'; return; }
    var maxActive = Math.max.apply(null, projs.map(function (p) { return p.active; }));

    el.innerHTML = projs.map(function (p) {
        var color = projectColor(p.name);
        var pct   = maxActive ? Math.round(p.active / maxActive * 100) : 0;
        var idle  = p.idle ? fmt(p.idle) + ' idle' : '';

        var seqHtml = '';
        if (p.seqs && p.seqs.length) {
            seqHtml = '<div class="seq-rows">' +
                p.seqs.filter(function (s) { return s.active > 0; }).map(function (s) {
                    return '<div class="seq-row">' +
                        '<span class="seq-name">↳ ' + s.name + '</span>' +
                        '<span class="seq-time">' + fmt(s.active) + '</span></div>';
                }).join('') + '</div>';
        }

        return '<div class="proj-row">' +
            '<div class="proj-name" style="color:' + color + '">' + p.name + '</div>' +
            '<div class="proj-meta"><span class="proj-time">' + fmt(p.active) + '</span>' +
            '<span class="proj-idle">' + idle + '</span></div>' +
            (showBars ? '<div class="bar-bg"><div class="bar-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' : '') +
            seqHtml + '</div>';
    }).join('');
}

function renderSessions(sessions) {
    var el = document.getElementById('sessions-list');
    if (!sessions.length) { el.innerHTML = '<div class="empty">No sessions yet.</div>'; return; }

    var html = '', lastDate = null;
    sessions.slice(0, 30).forEach(function (s) {
        var dateKey = s.start.slice(0, 10);
        if (dateKey !== lastDate) {
            var today = dateStr(0), yest = dateStr(1);
            var label = dateKey === today ? 'Today' : dateKey === yest ? 'Yesterday' : dateKey;
            html += '<div class="day-label">' + label + '</div>';
            lastDate = dateKey;
        }
        var color = projectColor(s.project);
        var range = fmtTime(s.start) + ' – ' + fmtTime(s.end);
        var seqLine = s.sequence ? '<div class="session-seq">↳ ' + s.sequence + '</div>' : '';
        var idleLine = s.idle ? '<div class="session-idle">' + fmt(s.idle) + ' idle</div>' : '';
        html += '<div class="session">' +
            '<div class="session-bar" style="background:' + color + '"></div>' +
            '<div><div class="session-name">' + s.project + '</div>' + seqLine +
            '<div class="session-range">' + range + '</div></div>' +
            '<div><div class="session-dur">' + fmt(s.active) + '</div>' + idleLine + '</div>' +
            '</div>';
    });
    el.innerHTML = html;
}

// ── poll Premiere ─────────────────────────────────────────────────────────

var _lastProject = null, _lastSeq = null, _lastStatus = 'off';

function poll() {
    if (!cs) return;
    cs.evalScript('getProjectInfo()', function (result) {
        var info;
        try { info = JSON.parse(result); } catch (e) { return; }
        if (!info || !info.project) return;

        _lastProject = info.project;
        _lastSeq     = info.sequence || null;

        // Write tick directly to JSONL file
        if (_fs && BASE) {
            try {
                // "active"  = Premiere is the focused app AND user has been active recently
                // "idle"    = Premiere is focused but user inactive, OR Premiere is in background
                // background ticks are still written so open-but-not-focused time is visible
                var focused       = document.hasFocus();
                var recentActivity = (Date.now() - _lastActivity) < 5 * 60 * 1000;
                var status = (focused && recentActivity) ? 'active' : 'idle';
                var ts    = (function() {
                    var d = new Date(), pad = function(n){return n<10?'0'+n:''+n;};
                    return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+
                           ' '+pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
                })();
                var tick  = JSON.stringify({ ts: ts, app: 'Adobe Premiere',
                    project: info.project, seq: info.sequence || null,
                    status: status, source: 'cep' });
                var dataDir = _path.join(BASE, 'data');
                if (!_fs.existsSync(dataDir)) _fs.mkdirSync(dataDir, { recursive: true });
                _fs.appendFileSync(_path.join(dataDir, ts.slice(0,10) + '.jsonl'), tick + '\n');
                _lastStatus = status;
            } catch (e) { console.log('[PremLogger] write tick failed:', e.message); }
        }

        updateNowBar(info.project, info.sequence, _lastStatus);
    });
}

function updateNowBar(project, seq, status) {
    var dot = document.getElementById('status-dot');
    var np  = document.getElementById('now-project');
    var ns  = document.getElementById('now-seq');
    if (dot) { dot.className = 'status-dot ' + (status || ''); }
    if (np)  np.textContent = project || '—';
    if (ns)  ns.textContent = seq ? '↳ ' + seq : '';
}

// ── idle tracking ─────────────────────────────────────────────────────────

var _lastActivity = Date.now();
document.addEventListener('mousemove', function () { _lastActivity = Date.now(); });
document.addEventListener('keydown',   function () { _lastActivity = Date.now(); });

// ── full refresh ──────────────────────────────────────────────────────────

function refresh() {
    var todayTicks   = readDay(dateStr(0));
    var recentTicks  = readRange(7);

    renderProjects('today-list',   byProjectAndSeq(todayTicks),  true);
    renderSessions(toSessions(recentTicks));
    renderProjects('alltime-list', byProjectAndSeq(readRange(90)), false);
}

// ── init ──────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {
    poll();
    refresh();
    setInterval(poll, 10000);
    setInterval(refresh, 30000);

    var btn = document.getElementById('refresh-btn');
    if (btn) btn.addEventListener('click', function () { poll(); refresh(); });
});
