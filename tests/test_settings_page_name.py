"""The page where keys, connections and linked chats live is called Settings. Server messages and
the chat bot's text are read by people, so none of them may send a person to the old name."""

from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src" / "between_jobs"
CONTRIBUTING = Path(__file__).parent.parent / "CONTRIBUTING.md"


def test_no_server_text_names_the_old_integrations_page() -> None:
    offenders = [
        f"{path.relative_to(SOURCE)}:{number}"
        for path in sorted(SOURCE.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if "Integrations" in line
    ]

    assert offenders == []


def test_contributing_names_the_settings_page() -> None:
    text = CONTRIBUTING.read_text()

    assert "Integrations page" not in text
    assert "Settings page" in text
