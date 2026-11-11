from types import SimpleNamespace

from app import bot, config


def _update(uid):
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid))


def _labels(markup):
    return [b.text for row in markup.keyboard for b in row]


def test_librarian_gets_librarian_buttons(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [42])
    labels = _labels(bot.main_keyboard(None, _update(42)))
    assert bot.BTN_A_HOME in labels and bot.BTN_A_LOANS in labels and bot.BTN_A_FIND in labels
    assert bot.BTN_REGISTER not in labels and bot.BTN_LINK not in labels


def test_member_and_guest_keyboards_unchanged(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [42])
    guest = _labels(bot.main_keyboard(None, _update(7)))
    assert bot.BTN_REGISTER in guest and bot.BTN_A_HOME not in guest
    assert bot.BTN_REGISTER in _labels(bot.main_keyboard(None))
