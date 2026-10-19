import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest import TestCase, mock
from tempfile import TemporaryDirectory
from pathlib import Path

from friendsbot.discord_reporting import (Reporter, BRUSSELS, collect_account, fetch_pages,
                                          recap_text, period_start, period_next, chunks, send_webhook)


def when(s):
    return datetime.fromisoformat(s)


def row(**changes):
    r = dict(id='sandbox:acc:p', position_id='p', environment='SANDBOX', market='binance|BTCUSDT',
             side='long', entry=100, stop=98, initial_size=10, size=10, risk=20,
             opened=when('2026-10-19T12:00:00+00:00').timestamp()*1000,
             closed=None, exit=None, reason=None, net=0, fees=0, funding=0, r=0,
             partial_tp=True, target_text='TP1: 104 (50% at 2R); runner EMA 21.',
             exit_fills=[],current_stop=98)
    return {**r,**changes}


class ReportingTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.sqlite3'
        self.sent=[]
        self.reporter=Reporter(self.path,'Atlas','fake',lambda u,n,b:self.sent.append(b))
        self.patch=mock.patch('friendsbot.discord_reporting.time.sleep')
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_entry_partial_be_close_and_restart_are_deduplicated(self):
        self.reporter.poll([],when('2026-10-19T11:00:00+00:00'))
        self.reporter.poll([row()],when('2026-10-19T12:01:00+00:00'))
        partial=row(size=5,current_stop=100,exit_fills=[dict(id='tp',at=1,size=5,price=104,tp=True)])
        self.reporter.poll([partial],when('2026-10-19T13:00:00+00:00'))
        closed={**partial,'closed':when('2026-10-19T14:00:00+00:00').timestamp()*1000,
                'net':19.5,'r':0.975,'exit':100,'reason':'stop_loss','fees':0.5}
        self.reporter.poll([closed],when('2026-10-19T14:01:00+00:00'))
        self.reporter.poll([closed],when('2026-10-19T14:02:00+00:00'))
        self.assertEqual(len(self.sent),4)
        self.assertIn('ENTRY FILLED',self.sent[0])
        self.assertIn('TP1 FILLED',self.sent[1])
        self.assertIn('BE',self.sent[2])
        self.assertIn('CLOSED',self.sent[3])
        self.assertIn('19.50',self.sent[3])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('select count(*) from trades').fetchone()[0],1)

    def test_send_failure_retains_outbox_and_does_not_block_collection(self):
        self.reporter.poll([],when('2026-10-19T11:00:00+00:00'))
        self.reporter.sender=mock.Mock(side_effect=RuntimeError('offline'))
        with self.assertRaises(RuntimeError):
            self.reporter.poll([row()],when('2026-10-19T12:01:00+00:00'))
        self.reporter.sender=lambda u,n,b:self.sent.append(b)
        self.reporter.poll([row()],when('2026-10-19T12:02:00+00:00'))
        self.assertEqual(len(self.sent),1)

    def test_old_history_not_spammed_but_current_position_announced(self):
        closed=row(closed=when('2026-10-19T14:00:00+00:00').timestamp()*1000)
        self.reporter.poll([closed],when('2026-10-20T12:00:00+00:00'))
        self.assertFalse(self.sent)
        self.reporter.poll([row(id='other',position_id='other')],when('2026-10-20T12:01:00+00:00'))
        self.assertIn('OPEN POSITION',self.sent[0])

    def test_recaps_dst_and_downtime_catchup_once(self):
        self.reporter.poll([],when('2026-10-19T11:00:00+00:00'))
        self.reporter.poll([],when('2026-11-02T00:00:00+00:00'))
        # October 26 + November 2 weekly boundaries; November 1 monthly boundary.
        self.assertEqual(sum('Weekly recap' in s for s in self.sent),2)
        self.assertEqual(sum('Monthly recap' in s for s in self.sent),1)
        n=len(self.sent)
        self.reporter.poll([],when('2026-11-02T00:00:01+00:00'))
        self.assertEqual(len(self.sent),n)
        start=period_start(when('2026-10-25T12:00:00+00:00'),'weekly')
        end=period_next(start,'weekly')
        self.assertEqual((end.astimezone(timezone.utc)-start.astimezone(timezone.utc)).total_seconds()/3600,169)
        self.assertEqual(period_next(when('2028-02-01T00:00:00+01:00').astimezone(BRUSSELS),'monthly').day,1)

    def test_winrate_partial_exits_and_boundary(self):
        start=when('2026-10-19T00:00:00+02:00');end=period_next(start,'weekly')
        at=when('2026-10-20T12:00:00+00:00').timestamp()*1000
        rows=[row(id='win',net=19.5,r=.975,closed=at),row(id='loss',net=-20,r=-1,closed=at),
              row(id='be',net=0,r=0,closed=at),row(id='future',net=100,r=5,closed=end.timestamp()*1000),row(id='open')]
        text=recap_text(rows,start,end,'weekly')
        self.assertIn('Closed: 3 | Wins: 1 | Losses: 1 | BE: 1',text)
        self.assertIn('50.0%',text)
        self.assertIn('$-0.50',text)
        self.assertIn('Open at period end: 2',text)
        parts=list(chunks(text+'\n'+'x'*5000))
        self.assertTrue(all(len(p)<=1900 for p in parts))

    def test_webhook_confirmed_no_mentions_or_leaked_token(self):
        response=mock.MagicMock();response.__enter__.return_value.status=200
        with mock.patch('urllib.request.urlopen',return_value=response) as send:
            send_webhook('https://discord.com/api/webhooks/123/secret','Atlas','hi @everyone')
        with mock.patch('urllib.request.urlopen',return_value=response):
            send_webhook('https://discordapp.com/api/webhooks/123/secret','Atlas','legacy URL')
        req=send.call_args.args[0]
        self.assertIn('wait=true',req.full_url)
        self.assertEqual(json.loads(req.data)['allowed_mentions'],{'parse':[]})
        with self.assertRaises(ValueError):
            send_webhook('http://example.com/secret','Atlas','hello')


class CollectionTests(TestCase):
    def test_pagination_and_fills_ignore_manual_other_market_and_rejected(self):
        opened=1792411200000
        def order(**kw):
            return dict(id='entry',market_id='binance|BTCUSDT',side='buy',status='filled',
                        reduce_only=False,settled_at=opened,client_order_id='ours',filled_size=10,**kw)
        orders=[order(),{**order(),'id':'manual','client_order_id':'manual'},
                {**order(),'id':'rejected','status':'rejected'},
                {**order(),'id':'other','market_id':'other'},
                dict(id='tp',market_id='binance|BTCUSDT',reduce_only=True,status='filled',type='take_profit',
                     position_id='p',filled_size=5,avg_fill_price=104,settled_at=opened+1000)]
        p=dict(id='p',market_id='binance|BTCUSDT',side='long',entry_price=100,size=5,opened_at=opened,
               closed_at=opened+2000,realized_pnl=30,fees=2,funding=-1,status='closed')
        client=mock.Mock();client.environment='test'
        def call(method,path,q):
            if path=='/v1/orders':
                return {'data':orders[2:] if q['cursor'] else orders[:2], 'has_more':not q['cursor'],'next_cursor':'next'}
            return {'data':[p] if q['status']=='closed' else [],'has_more':False}
        client._call.side_effect=call
        rows=collect_account(client,'acc',lambda cid:{'stop':98} if cid=='ours' else None,
                             lambda *a:dict(target_text='TP1',partial_tp=True))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['net'],27)  # signed funding, never subtract fees twice
        self.assertEqual(rows[0]['risk'],20) # original size, not remaining size 5
        self.assertEqual(rows[0]['r'],1.35)
        self.assertEqual(len(rows[0]['exit_fills']),1)

    def test_broken_pagination_is_an_error(self):
        client=mock.Mock()
        client._call.return_value={'data':[],'has_more':True,'next_cursor':None}
        with self.assertRaises(ValueError):
            fetch_pages(client,'/v1/orders',{})
