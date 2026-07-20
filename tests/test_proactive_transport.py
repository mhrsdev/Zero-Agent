import asyncio
from zero.proactive_transport import MockProactiveTransport, Outbox, TelegramProactiveTransport, TransportResult, select_transport
from zero.storage import ZeroStore


def test_outbox_reservation_is_atomic_and_recovery_is_ambiguous(tmp_path):
 s=ZeroStore(str(tmp_path/'x.db'));o=Outbox(s);assert o.reserve('c','a',100)== 'pf:c';assert o.reserve('c','b',101) is None;o.recover(1001)
 with s._conn() as c:assert c.execute("select send_state from proactive_followup_outbox").fetchone()[0]=='ambiguous'
 assert o.reserve('c','b',1002) is None

def test_outbox_receipt_persists_after_reopen(tmp_path):
 path=str(tmp_path/'x.db');s=ZeroStore(path);o=Outbox(s);key=o.reserve('c','a',100);o.complete(key,TransportResult(True,'42'))
 with ZeroStore(path)._conn() as c:assert tuple(c.execute('select send_state,receipt from proactive_followup_outbox').fetchone())==('sent','42')

def test_send_disabled_selects_mock_and_never_calls_telegram(tmp_path,monkeypatch):
 class Client:
  calls=0
  async def send_message(self,*a):self.calls+=1
 monkeypatch.setenv('ZERO_PROACTIVE_FOLLOWUP_SEND_ENABLED','false');client=Client();transport=select_transport(client)
 assert isinstance(transport,MockProactiveTransport);asyncio.run(transport.send(-1,'x','k'));assert client.calls==0

def test_telegram_transport_is_policy_free():
 class Client:
  calls=0
  async def send_message(self,*a):self.calls+=1;return type('R',(),{'id':9})()
 client=Client();r=asyncio.run(TelegramProactiveTransport(client).send(-1,'x','k'));assert r.success and r.receipt=='9' and client.calls==1
