'use strict';
/**
 * server.js — lightweight HTTP server + data layer.
 * No Flask, no SQLite, no external dependencies.
 * Data lives in daily .jsonl files: data/YYYY-MM-DD.jsonl
 */

var http = require('http');
var fs   = require('fs');
var path = require('path');

var DATA_DIR        = null;
var DASHBOARD_FILE  = null;
var CATEGORIES_FILE = null;
var TICK_SEC        = 10;
var SESSION_GAP     = 600; // 10 minutes

// ── init ──────────────────────────────────────────────────────────────────

exports.init = function (basePath) {
    DATA_DIR        = path.join(basePath, 'data');
    DASHBOARD_FILE  = path.join(basePath, 'templates', 'index.html');
    CATEGORIES_FILE = path.join(DATA_DIR, 'categories.json');
    if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
};

// ── data helpers ──────────────────────────────────────────────────────────

function todayStr() {
    return new Date().toISOString().slice(0, 10);
}

function dateStr(daysAgo) {
    var d = new Date();
    d.setDate(d.getDate() - daysAgo);
    return d.toISOString().slice(0, 10);
}

function readDay(ds) {
    var f = path.join(DATA_DIR, ds + '.jsonl');
    if (!fs.existsSync(f)) return [];
    return fs.readFileSync(f, 'utf8').split('\n').filter(Boolean).map(function (l) {
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

exports.writeTick = function (tick) {
    var f = path.join(DATA_DIR, todayStr() + '.jsonl');
    fs.appendFileSync(f, JSON.stringify(tick) + '\n');
};

// ── categories ────────────────────────────────────────────────────────────

var DEFAULT_CATS = {
    'Adobe Premiere': 'work',
    'After Effects':  'work',
    'DaVinci Resolve':'work',
    'Photoshop':      'work',
    'Audition':       'work',
};

function readCats() {
    if (!fs.existsSync(CATEGORIES_FILE)) return Object.assign({}, DEFAULT_CATS);
    try { return JSON.parse(fs.readFileSync(CATEGORIES_FILE, 'utf8')); }
    catch (e) { return Object.assign({}, DEFAULT_CATS); }
}

function saveCats(cats) {
    fs.writeFileSync(CATEGORIES_FILE, JSON.stringify(cats, null, 2));
}

// ── aggregation ───────────────────────────────────────────────────────────

function byProject(ticks) {
    var acc = {};
    ticks.filter(function (t) { return t.app === 'Adobe Premiere'; }).forEach(function (t) {
        if (!acc[t.project]) acc[t.project] = { active: 0, idle: 0 };
        acc[t.project][t.status] = (acc[t.project][t.status] || 0) + TICK_SEC;
    });
    return Object.keys(acc).map(function (n) {
        return { name: n, active: acc[n].active, idle: acc[n].idle };
    }).sort(function (a, b) { return b.active - a.active; });
}

function byApp(ticks) {
    var cats = readCats();
    var acc  = {};
    ticks.forEach(function (t) {
        if (!acc[t.app]) acc[t.app] = { active: 0, idle: 0 };
        acc[t.app][t.status] = (acc[t.app][t.status] || 0) + TICK_SEC;
    });
    return Object.keys(acc).map(function (a) {
        return { app: a, active: acc[a].active, idle: acc[a].idle, category: cats[a] || null };
    }).sort(function (a, b) { return (b.active + b.idle) - (a.active + a.idle); });
}

function toSessions(ticks) {
    var cats = readCats();
    var rows = ticks.filter(function (t) { return cats[t.app] !== 'ignore'; });
    var sessions = [], cur = null;

    rows.forEach(function (t) {
        var ts = new Date(t.ts.replace(' ', 'T'));
        if (!cur || t.app !== cur.app || (ts - cur._ts) / 1000 > SESSION_GAP) {
            if (cur) flush(cur, sessions);
            cur = { app: t.app, project: t.project, sequence: t.seq || null,
                    start: t.ts, active: 0, idle: 0, _ts: ts, _end: t.ts };
        }
        cur[t.status] = (cur[t.status] || 0) + TICK_SEC;
        cur._ts  = ts;
        cur._end = t.ts;
    });
    if (cur) flush(cur, sessions);
    return sessions.reverse();
}

function flush(cur, sessions) {
    sessions.push({
        app: cur.app, project: cur.project, sequence: cur.sequence,
        start: cur.start, end: cur._end,
        active: cur.active, idle: cur.idle,
        duration: cur.active + cur.idle
    });
}

// ── HTTP server ───────────────────────────────────────────────────────────

exports.start = function (port) {
    var srv = http.createServer(function (req, res) {
        var url = req.url.split('?')[0];

        // Dashboard HTML
        if (url === '/') {
            try {
                res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
                res.end(fs.readFileSync(DASHBOARD_FILE, 'utf8'));
            } catch (e) {
                res.writeHead(500); res.end('Dashboard file not found');
            }
            return;
        }

        res.setHeader('Content-Type', 'application/json');
        res.setHeader('Access-Control-Allow-Origin', '*');

        // POST /api/categories
        if (url === '/api/categories' && req.method === 'POST') {
            var body = '';
            req.on('data', function (c) { body += c; });
            req.on('end', function () {
                try {
                    var data = JSON.parse(body);
                    var cats = readCats();
                    if (data.category) cats[data.app] = data.category;
                    else delete cats[data.app];
                    saveCats(cats);
                    res.writeHead(200); res.end(JSON.stringify({ ok: true }));
                } catch (e) { res.writeHead(400); res.end(JSON.stringify({ error: e.message })); }
            });
            return;
        }

        try {
            var result;

            if (url === '/api/status') {
                var cutoff = Date.now() - 15000;
                var last   = readDay(todayStr()).filter(function (t) {
                    return new Date(t.ts.replace(' ', 'T')).getTime() >= cutoff;
                }).pop();
                result = last
                    ? { tracking: true,  project: last.project, status: last.status, last_tick: last.ts }
                    : { tracking: false };
            }
            else if (url === '/api/today') {
                result = { date: todayStr(), projects: byProject(readDay(todayStr())) };
            }
            else if (url === '/api/week') {
                var allP = {}, days = [];
                for (var i = 6; i >= 0; i--) {
                    var ds    = dateStr(i);
                    var projs = {};
                    readDay(ds).filter(function (t) { return t.app === 'Adobe Premiere'; }).forEach(function (t) {
                        projs[t.project] = (projs[t.project] || 0) + TICK_SEC;
                        allP[t.project]  = true;
                    });
                    days.push({
                        date: ds,
                        projects: projs,
                        total_active: Object.keys(projs).reduce(function (s, k) { return s + projs[k]; }, 0)
                    });
                }
                days.reverse();
                result = { days: days, all_projects: Object.keys(allP) };
            }
            else if (url === '/api/sessions') {
                result = toSessions(readRange(7));
            }
            else if (url === '/api/alltime') {
                result = byProject(readRange(365));
            }
            else if (url === '/api/apps_today') {
                result = byApp(readDay(todayStr()));
            }
            else if (url === '/api/categories') {
                result = readCats();
            }
            else {
                res.writeHead(404); res.end(JSON.stringify({ error: 'not found' })); return;
            }

            res.writeHead(200); res.end(JSON.stringify(result));
        } catch (e) {
            res.writeHead(500); res.end(JSON.stringify({ error: e.message }));
        }
    });

    srv.on('error', function (e) {
        if (e.code !== 'EADDRINUSE') console.error('Server error:', e.message);
    });
    srv.listen(port, '127.0.0.1');
    return srv;
};
