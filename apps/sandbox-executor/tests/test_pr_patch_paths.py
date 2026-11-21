"""The paths a pull request's patch changes (pr_patch_paths.py): renames, deletions, quoting."""

from __future__ import annotations

from sandbox_executor.pr_patch_paths import patch_paths

from tests.flow.helpers import patch_of


def test_modified_files_are_listed_once_in_order():
    assert patch_paths(patch_of(["src/a.py", "docs/b.md"])) == ["src/a.py", "docs/b.md"]


def test_a_rename_lists_its_source_and_destination():
    patch = (
        "diff --git a/old/x.py b/new/x.py\nsimilarity index 90%\nrename from old/x.py\nrename to new/x.py\n"
        "--- a/old/x.py\n+++ b/new/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert patch_paths(patch) == ["old/x.py", "new/x.py"]


def test_a_deletion_lists_the_deleted_path():
    patch = "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n"
    assert patch_paths(patch) == ["gone.py"]


def test_a_copy_lists_only_its_destination():
    patch = "diff --git a/a.py b/b.py\nsimilarity index 100%\ncopy from a.py\ncopy to b.py\n"
    assert patch_paths(patch) == ["b.py"]


def test_a_mode_only_change_and_a_binary_file_are_read_from_the_header():
    patch = (
        "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
        "diff --git a/img/logo.png b/img/logo.png\nindex 1..2 100644\n"
        "Binary files a/img/logo.png and b/img/logo.png differ\n"
    )
    assert patch_paths(patch) == ["run.sh", "img/logo.png"]


def test_spaced_and_quoted_paths_are_decoded():
    patch = (
        "diff --git a/dir with space/a b.py b/dir with space/a b.py\n"
        "--- a/dir with space/a b.py\t\n+++ b/dir with space/a b.py\t\n"
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        '--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n'
    )
    assert patch_paths(patch) == ["dir with space/a b.py", "café.py"]
