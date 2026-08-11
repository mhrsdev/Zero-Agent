from conftest import CONFIG_EXAMPLE
from zero.config import ZeroConfig
from zero.prompts import build_reply_prompt

def prompt(memory=''):
    c=ZeroConfig.load(CONFIG_EXAMPLE)
    return build_reply_prompt(c,mode='normal',sender_label='u',user_text='من کی هستم؟',reply_text='',recent=[],group_summary='',memory_context=memory,chat_id=1,sender_id=2)

def test_identity_prompt_requires_evidence_and_first_person():
    p=prompt()
    assert 'نقش، رابطه، نام، ترجیح یا سابقه نساز' in p
    assert 'اطلاعات مطمئن یا خاطرهٔ مرتبطی در دسترس نیست' in p
    assert 'do not invent user identity' in p
    assert 'به‌طور پیش‌فرض اول‌شخص' in p

def test_identity_prompt_keeps_only_explicit_memory_context():
    p=prompt('[fact] نام کاربر مهراس است\n[project] روی Zero کار می‌کند')
    assert '[fact] نام کاربر مهراس است' in p
    assert 'صاحب‌خونه' not in p and 'تو صاحب منی' not in p
