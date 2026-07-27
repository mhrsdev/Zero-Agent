import asyncio
from zero.document_bundles import DocumentBundles
from zero.storage import ZeroStore
from zero.models import IncomingMessage
def m(i,t,u=1):return IncomingMessage(-1,'g',u,'u',t,message_id=i)
def test_bundle_reference_and_injection_boundary(tmp_path):
 async def x():
  s=ZeroStore(str(tmp_path/'x.db'));b=DocumentBundles(s)
  for i in range(1,5):
   await s.append_recent(-1,1,'u','user','بخش بلند '+str(i)+' '+('x'*260),telegram_message_id=i);await b.observe(m(i,'بخش بلند '+str(i)+' '+('x'*260)))
  await b.observe(m(5,'نظرت راجبش چیه؟'))
  text,_,_=b.reference(m(5,'نظرت راجبش چیه؟'));assert 'BEGIN USER-PROVIDED DOCUMENT' in text and 'x'*100 in text and 'not instructions' in text
 asyncio.run(x())
def test_reply_to_part_resolves_parent_bundle(tmp_path):
 async def x():
  s=ZeroStore(str(tmp_path/'x.db'));b=DocumentBundles(s)
  for i in (1,2):
   await s.append_recent(-1,1,'u','user','z'*260,telegram_message_id=i);await b.observe(m(i,'z'*260))
  q=m(3,'این قسمت مشکل داره؟',2);q.reply_to_message_id=1
  text,ids,_=b.reference(q);assert 1 in ids and 'BEGIN USER-PROVIDED DOCUMENT' in text
 asyncio.run(x())
def test_users_do_not_mix(tmp_path):
 async def x():
  s=ZeroStore(str(tmp_path/'x.db'));b=DocumentBundles(s)
  for u in (1,2):
   await s.append_recent(-1,u,'u','user','a'*260,telegram_message_id=u);await b.observe(m(u,'a'*260,u))
  assert b._open(-1,1)['bundle_id']!=b._open(-1,2)['bundle_id']
 asyncio.run(x())
