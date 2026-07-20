import pytest
from zero.world_model import WorldModel

def test_world_entities_aliases_relations_and_personal_data_guard(tmp_path):
 w=WorldModel(tmp_path/'w.db'); z=w.entity('Zero','system'); t=w.entity('Telegram Search','component'); p=w.entity('Telethon','library')
 w.alias('zero',z,['docs:1']); w.relation(z,'has_component',t,['arch:1']); w.relation(t,'uses_library',p,['code:2'])
 data=w.retrieve('zero'); assert data['entity']['canonical_name']=='Zero' and data['relations'][0]['object_name']=='Telegram Search'
 with pytest.raises(ValueError): w.entity('secret token','person')
 with pytest.raises(ValueError): w.alias('zero',p,['conflict'])
 with pytest.raises(ValueError): w.relation(z,'bad',p,[])
