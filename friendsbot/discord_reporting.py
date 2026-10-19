"""Read-only Discord reporting with a durable SQLite journal/outbox.

The copy in the other bot repository intentionally has no imports from this project.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BRUSSELS = ZoneInfo('Europe/Brussels')
UTC = timezone.utc


def stamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)


def number(value):
    return float(value or 0)


def fmt(value, digits=2):
    return 'unknown' if value is None else f'{float(value):,.{digits}f}'


def period_start(now, kind):
    local = now.astimezone(BRUSSELS).replace(hour=0, minute=0, second=0, microsecond=0)
    return local - timedelta(days=local.weekday()) if kind == 'weekly' else local.replace(day=1)


def period_next(start, kind):
    if kind == 'weekly':
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def fetch_pages(client, path, query):
    """Never silently produce a recap from a truncated first page."""
    call = getattr(client, '_call', None) or client.call
    out, cursor, seen = [], None, set()
    for _ in range(1000):
        page = call('GET', path, {**query, 'limit': 100, 'cursor': cursor})
        out.extend(page['data'])
        if not page.get('has_more'):
            return out
        cursor = page.get('next_cursor')
        if not cursor or cursor in seen:
            raise ValueError('Invalid history pagination; reporting skipped')
        seen.add(cursor)
    raise ValueError('History pagination limit reached; reporting skipped')


def collect_account(client, account, parse_entry, targets):
    """Match only this bot's entries to complete position lifecycles.

    Prefer a position id; timestamp fallback requires the same market, side and a
    fill within one minute of opening. Ambiguous/manual positions are ignored.
    """
    orders = fetch_pages(client, '/v1/orders', {'account_id': account})
    positions = fetch_pages(client, '/v1/positions', {'account_id': account, 'status': 'closed'})
    positions += fetch_pages(client, '/v1/positions', {'account_id': account, 'status': 'open'})
    entries = []
    for o in orders:
        meta = parse_entry(o.get('client_order_id'))
        if meta and o.get('status') in ('filled', 'partially_filled') and not o.get('reduce_only'):
            entries.append((o, meta))
    rows = []
    for p in positions:
        opened = p.get('opened_at')
        if not opened:
            continue
        def eligible(o):
            return (o.get('market_id') == p['market_id'] and
                    o.get('side') == ('buy' if p['side'] == 'long' else 'sell'))
        candidates = [(o, m) for o, m in entries if eligible(o) and o.get('position_id') == p['id']]
        if not candidates:
            candidates = [(o, m) for o, m in entries if eligible(o) and not o.get('position_id') and
                          abs(number(o.get('settled_at') or o.get('requested_at')) - opened) <= 60000]
        if len(candidates) != 1:
            continue
        o, meta = candidates[0]
        entry = number(p.get('entry_price'))
        initial_size = number(o.get('filled_size') or o.get('size'))
        stop = number(meta['stop'])
        risk = abs(entry - stop) * initial_size
        closed = p.get('status') == 'closed' or bool(p.get('closed_at'))
        # Official journal definition: realized P&L - fees + signed funding.
        net = number(p.get('realized_pnl')) - abs(number(p.get('fees'))) + number(p.get('funding'))
        row = dict(id=f"{client.environment}:{account}:{p['id']}", position_id=p['id'],
                   environment='SANDBOX' if client.environment == 'test' else 'LIVE',
                   market=p['market_id'], side=p['side'], entry=entry, stop=stop,
                   initial_size=initial_size, size=number(p.get('size')), risk=risk,
                   opened=opened, closed=p.get('closed_at') if closed else None,
                   exit=p.get('exit_price'), reason=p.get('close_reason'),
                   net=net, fees=number(p.get('fees')), funding=number(p.get('funding')),
                   r=net / risk if risk else None, strategy=meta.get('strategy') or meta.get('tf'),
                   **targets(entry, stop, p['side'], o, meta))
        exits = []
        for x in orders:
            if not x.get('reduce_only') or x.get('market_id') != p['market_id']:
                continue
            at = number(x.get('settled_at') or x.get('requested_at'))
            linked = x.get('position_id') == p['id']
            fallback = not x.get('position_id') and opened <= at <= number(p.get('closed_at') or float('inf'))
            if linked or fallback:
                exits.append(x)
        row['exit_fills'] = [dict(id=x['id'], at=x.get('settled_at') or x.get('requested_at'),
                                  size=x.get('filled_size') or x.get('size'), price=x.get('avg_fill_price') or x.get('fill_price'),
                                  tp=(x.get('type') in ('take_profit', 'take_market') or
                                      x.get('mode') in ('take_profit', 'take_market')))
                             for x in exits if x.get('status') in ('filled', 'partially_filled')]
        stops = [x for x in exits if x.get('status') in ('pending', 'resting', 'working') and
                 (x.get('type') == 'stop_market' or x.get('mode') == 'stop_market')]
        row['current_stop'] = number(stops[0].get('trigger_price')) if len(stops) == 1 else None
        rows.append(row)
    return rows


def trade_text(row, event):
    header = f"**{row['environment']} · {event} · {row['market'].split('|')[-1]} {row['side'].upper()}**"
    lines = [header, f"Entry: {fmt(row['entry'])} | Initial SL: {fmt(row['stop'])}",
             row['target_text'], f"Initial size: {fmt(row['initial_size'], 6)} | Risk: ${fmt(row['risk'])}"]
    if row.get('strategy'):
        lines.append(f"Strategy / timeframe: {row['strategy']}")
    if event == 'CLOSED':
        lines.append(f"Exit: {fmt(row['exit'])} | Reason: {row.get('reason') or 'not supplied'}")
        lines.append(f"Net P&L: ${fmt(row['net'])} | Result: {fmt(row['r'])}R")
        lines.append(f"Fees: ${fmt(row['fees'])} | Funding (signed): ${fmt(row['funding'])}")
    lines.append(f"Trade: `{row['position_id']}`")
    return '\n'.join(lines)


def recap_text(rows, start, end, kind):
    closed = sorted([r for r in rows if r.get('closed') and start <= stamp(r['closed']) < end], key=lambda r:r['closed'])
    opened = [r for r in rows if stamp(r['opened']) < end and
              (not r.get('closed') or stamp(r['closed']) >= end)]
    wins = sum(r['net'] > 0.005 for r in closed)
    losses = sum(r['net'] < -0.005 for r in closed)
    be = len(closed) - wins - losses
    winrate = f'{100*wins/(wins+losses):.1f}%' if wins+losses else 'n/a'
    net = sum(r['net'] for r in closed)
    known_r = [r['r'] for r in closed if r.get('r') is not None]
    envs = ', '.join(sorted({r['environment'] for r in rows})) or 'SANDBOX'
    lines = [f"**{kind.title()} recap · {envs}**",
             f"{start:%d %b %Y} – {(end-timedelta(seconds=1)):%d %b %Y} · Europe/Brussels",
             f"Closed: {len(closed)} | Wins: {wins} | Losses: {losses} | BE: {be}",
             f"Winrate (BE excluded): {winrate}",
             f"Net P&L: ${net:+,.2f} | Total: {sum(known_r):+.2f}R" +
             (f" ({len(closed)-len(known_r)} trades with unknown R)" if len(known_r)!=len(closed) else ''),
             f"Open at period end: {len(opened)} (excluded from closed-trade results)",
             'Partial exits are included in the complete trade result, counted on final close.']
    for r in closed:
        local = stamp(r['closed']).astimezone(BRUSSELS)
        lines.append(f"{local:%d/%m %H:%M} · {r['market'].split('|')[-1]} {r['side']} · "
                     f"{fmt(r['entry'])} → {fmt(r['exit'])} · ${r['net']:+.2f} / {fmt(r['r'])}R · "
                     f"{r.get('reason') or 'closed'} · {r['position_id']}")
    return '\n'.join(lines)


def chunks(text, limit=1900):
    part = ''
    for line in text.splitlines():
        while len(line) > limit:
            if part:
                yield part
                part = ''
            yield line[:limit]
            line = line[limit:]
        if len(part)+len(line)+1 > limit:
            yield part
            part = ''
        part += ('\n' if part else '') + line
    if part:
        yield part


def send_webhook(url, name, content):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('discord.com', 'discordapp.com', 'canary.discord.com', 'ptb.discord.com') or '/api/webhooks/' not in parsed.path:
        raise ValueError('Expected a Discord HTTPS webhook URL')
    query = dict(urllib.parse.parse_qsl(parsed.query))
    query['wait'] = 'true'
    target = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    body = json.dumps({'username': name, 'content': content, 'allowed_mentions': {'parse': []}}).encode()
    req = urllib.request.Request(target, data=body, headers={'Content-Type':'application/json', 'User-Agent':'TradingLegends/1'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f'Discord HTTP {response.status}')
    except urllib.error.HTTPError as error:
        # Do not log exceptions containing the secret webhook URL or response body.
        raise RuntimeError(f'Discord HTTP {error.code}; queued for next poll') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Discord connection failed; queued for next poll') from None


class Reporter:
    def __init__(self, path, name, url='', sender=send_webhook):
        self.path, self.name, self.url, self.sender = Path(path), name, url, sender

    def poll(self, rows, now=None):
        now = (now or datetime.now(UTC)).astimezone(UTC)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0)')
            db.commit()
            # Serialise polling processes sharing this file (also safe across a runner restart).
            db.execute('BEGIN IMMEDIATE')
            def get(key):
                r = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
                return r[0] if r else None
            def put(key, value):
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, value))
            def queue(key, body):
                for i, part in enumerate(chunks(body)):
                    db.execute('INSERT OR IGNORE INTO outbox(id,body) VALUES (?,?)', (f'{key}:{i}', part))
            started = get('started')
            first = started is None
            if first:
                started = now.isoformat()
                put('started', started)
                for kind in ('weekly','monthly'):
                    put(kind, period_start(now, kind).isoformat())
            for row in rows:
                prior = db.execute('SELECT body FROM trades WHERE id=?', (row['id'],)).fetchone()
                old = json.loads(prior[0]) if prior else None
                # Historical trades are journaled for recaps, without flooding Discord on first start.
                active = stamp(row['opened']) >= stamp(started)
                if not row.get('closed') and old is None:
                    queue('entry:'+row['id'], trade_text(row, 'OPEN POSITION' if not active else 'ENTRY FILLED'))
                elif active and old is None:
                    queue('entry:'+row['id'], trade_text(row, 'ENTRY FILLED (recovered)'))
                if active or (old and not old.get('closed')):
                    for fill in row.get('exit_fills', []):
                        label = 'TP1 FILLED' if fill['tp'] and row.get('partial_tp') else 'EXIT FILL'
                        queue('fill:'+row['id']+':'+fill['id']+':'+str(fill['size']),
                              f"**{row['environment']} · {label} · {row['market'].split('|')[-1]}**\n"
                              f"Size: {fmt(fill['size'],6)} | Fill: {fmt(fill['price'])}\nTrade: `{row['position_id']}`")
                    stop = row.get('current_stop')
                    if stop is not None and old and stop != old.get('current_stop'):
                        queue('sl:'+row['id']+':'+str(stop),
                              f"**{row['environment']} · SL UPDATE · {row['market'].split('|')[-1]}**\n"
                              f"SL: {fmt(stop)}" + (' · BE' if abs(stop-row['entry'])<1e-8 else '') + f"\nTrade: `{row['position_id']}`")
                    if row.get('closed'):
                        queue('closed:'+row['id'], trade_text(row, 'CLOSED'))
                db.execute('INSERT OR REPLACE INTO trades VALUES (?,?)', (row['id'], json.dumps(row)))
            all_rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM trades')]
            # Send immediately after the complete Sunday/month-end, and catch up after downtime.
            for kind in ('weekly', 'monthly'):
                start = stamp(get(kind)).astimezone(BRUSSELS)
                end = period_next(start, kind)
                while end <= now:
                    queue(kind+':'+start.isoformat(), recap_text(all_rows,start,end,kind))
                    put(kind, end.isoformat())
                    start, end = end, period_next(end, kind)
            db.commit()
            # Durable per-message acknowledgement. Discord has no idempotency token: an
            # ambiguous network failure after acceptance may duplicate one message on retry.
            if self.url:
                while True:
                    db.execute('BEGIN IMMEDIATE')
                    pending = db.execute('SELECT id,body FROM outbox WHERE sent=0 ORDER BY rowid LIMIT 1').fetchone()
                    if pending is None:
                        db.commit()
                        break
                    key, body = pending
                    self.sender(self.url, self.name, body)
                    db.execute('UPDATE outbox SET sent=1 WHERE id=?',(key,))
                    db.commit()
                    print(f"Discord delivered: {key.split(chr(58), 1)[0]}", flush=True)
                    time.sleep(0.5)


def loop(collect, reporter, interval=30):
    synced = False
    while True:
        try:
            rows = collect()
            reporter.poll(rows)
            if not synced:
                print(f"Discord reporting ready: {len(rows)} matched bot positions; durable journal active", flush=True)
                synced = True
        except Exception as error:
            # No URLs, tokens, account ids or API response bodies in logs.
            print(f'Discord reporting deferred ({type(error).__name__}); retry next poll', flush=True)
        time.sleep(interval)
