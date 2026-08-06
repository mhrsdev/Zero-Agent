from zero.brain import PROJECT_GROUP_CHAT_ID, PROJECT_REPO_REPLY, is_project_repo_request


def test_project_repo_phrases_are_detected():
    assert is_project_repo_request("زیرو پروژه گیت هابت چیه؟")
    assert is_project_repo_request("پروژه زیرو چیه؟")
    assert is_project_repo_request("Zero GitHub")
    assert not is_project_repo_request("گیت هاب این پروژه چیه؟")


def test_project_reply_is_fixed_and_group_scoped():
    assert PROJECT_GROUP_CHAT_ID == -1002042626209
    assert "https://github.com/mhrsdev/Zero-Agent" in PROJECT_REPO_REPLY
    assert "⭐" in PROJECT_REPO_REPLY
