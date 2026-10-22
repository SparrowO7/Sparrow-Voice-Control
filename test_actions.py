import actions
import router


def test_route_exposes_structured_action_parameters():
    intent = router.route("food4all ka pdf kholo")
    action = intent.to_action()

    assert action.as_dict()["action"] == "open_file"
    assert action.parameters == {"term": "food4all", "file_type": "pdf"}


def test_laya_or_keyword_action_has_no_shell_parameter():
    action = router.route("chrome kholo").to_action()

    assert action.name == "open_app"
    assert "shell" not in action.parameters
    assert "command" not in action.parameters


def test_destructive_key_is_blocked_until_confirmation(monkeypatch):
    intent = router.route("window band karo")
    assert intent.term == "alt+f4"

    result = actions.run(intent)

    assert not result.ok
    assert result.confirmation_required


def test_notepad_phonetic_aliases_normalize_to_notepad():
    for heard in ("open ipad", "mode pad kholo", "rootpad kholo"):
        intent = router.route(heard)
        assert intent.name == "open_app"
        assert intent.term == "notepad"


def test_compound_open_and_write_preserves_digits():
    assert router.split_compound("open notepad and write sparrow07") == [
        "open notepad", "type sparrow07"
    ]
    assert router.split_compound("notepad kholo and sparrow 070 kholo") == [
        "notepad kholo", "type sparrow 070"
    ]
