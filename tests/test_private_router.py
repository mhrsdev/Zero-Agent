import pytest

from zero.private_router import IDENTITY_REPLY, INTRODUCTION, ZeroPrivateRouter, asks_if_mehras
from zero.models import RouteResult


class FakeRouter:
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, max_output_tokens: int = 700) -> RouteResult:
        self.prompts.append(prompt)
        return RouteResult(text=self.text, provider='test', model='test', attempts=1)


@pytest.mark.asyncio
async def test_first_private_reply_is_transparently_disclosed():
    fake = FakeRouter('حتماً، بررسی می‌کنم.')
    private = ZeroPrivateRouter(config=object(), router=fake)  # config is unused with injected router

    reply = await private.reply(
        counterpart_label='کاربر الف', user_text='میشه پیگیری کنی؟',
        history=[{'dir': 'in', 'by': 'کاربر الف', 'text': 'سلام'}], already_disclosed=False,
    )

    assert reply.startswith(INTRODUCTION)
    assert reply.endswith('حتماً، بررسی می‌کنم.')
    assert 'کاربر الف' in fake.prompts[0]


@pytest.mark.asyncio
async def test_direct_identity_question_is_deterministic_and_skips_model():
    fake = FakeRouter('نباید اجرا شود')
    private = ZeroPrivateRouter(config=object(), router=fake)

    reply = await private.reply(
        counterpart_label='کاربر', user_text='خودتی؟', history=[], already_disclosed=True,
    )

    assert reply == IDENTITY_REPLY
    assert fake.prompts == []
    assert asks_if_mehras('مهراسی؟')


@pytest.mark.asyncio
async def test_private_prompt_receives_only_supplied_chat_history():
    fake = FakeRouter('پاسخ امن')
    private = ZeroPrivateRouter(config=object(), router=fake)

    await private.reply(
        counterpart_label='کاربر اول', user_text='موضوع چیه؟',
        history=[{'dir': 'in', 'by': 'کاربر اول', 'text': 'فقط تاریخچه همین چت'}], already_disclosed=True,
    )

    prompt = fake.prompts[0]
    assert 'فقط تاریخچه همین چت' in prompt
    assert 'چت دیگر' in prompt  # privacy constraint, not external history
    assert 'کاربر اول' in prompt


@pytest.mark.asyncio
async def test_impersonating_provider_output_fails_closed():
    private = ZeroPrivateRouter(config=object(), router=FakeRouter('من مهراسم، بگو.'))

    reply = await private.reply(
        counterpart_label='کاربر', user_text='سلام', history=[], already_disclosed=True,
    )

    assert reply == IDENTITY_REPLY
