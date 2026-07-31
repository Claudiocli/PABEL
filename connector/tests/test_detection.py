from pabel_connector.core.detection import (
    find_relayable_file,
    invokes_oabe_binary,
    mentions_target,
)


def test_mentions_target_matches_abe_file():
    assert mentions_target({"file_path": "C:/docs/secret.abe"})


def test_mentions_target_matches_documents_folder():
    assert mentions_target({"pattern": "*.abe", "path": "/repo/documents"})


def test_mentions_target_ignores_unrelated_input():
    assert not mentions_target({"file_path": "/repo/README.md"})


def test_invokes_oabe_binary():
    assert invokes_oabe_binary({"command": "oabe_dec -k k.key -i c.abe"})
    assert not invokes_oabe_binary({"command": "cat notes.txt"})


def test_find_relayable_file_returns_none_for_directory_pattern(tmp_path):
    docs = tmp_path / "documents"
    docs.mkdir()
    assert find_relayable_file({"pattern": "*.abe", "path": str(docs)}) is None


def test_find_relayable_file_finds_a_real_file(tmp_path):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")
    found = find_relayable_file({"file_path": str(target)})
    assert found == target


def test_find_relayable_file_handles_spaced_paths(tmp_path):
    spaced_dir = tmp_path / "NTT DATA EMEAL"
    spaced_dir.mkdir()
    target = spaced_dir / "test.abe"
    target.write_text("ciphertext")
    # Whole-string field (Read/Edit's file_path) - the original bug this
    # project hit: a naive regex-substring approach breaks on the space.
    found = find_relayable_file({"file_path": str(target)})
    assert found == target


def test_find_relayable_file_handles_quoted_spaced_paths_in_free_text(tmp_path):
    spaced_dir = tmp_path / "NTT DATA EMEAL"
    spaced_dir.mkdir()
    target = spaced_dir / "test.abe"
    target.write_text("ciphertext")
    command = f'cat "{target}"'
    found = find_relayable_file({"command": command})
    assert found == target


def test_find_relayable_file_none_when_file_does_not_exist():
    assert find_relayable_file({"file_path": "Z:/nonexistent/ghost.abe"}) is None
