# Releasing aiopnsense

## Normal release

1. Merge the release-ready changes into the default branch.
2. From that branch, run the **Release** workflow with one of these inputs:

   - To release an explicit tag (including every prerelease), provide an unused,
     valid `v`-prefixed tag, leave **bump** set to `none`, and set
     **prerelease** to match the tag. Tags with a suffix are prereleases; tags
     containing only numeric components are stable.
   - To make a stable automatic bump, leave **tag** blank, set **prerelease** to
     false, and choose `patch`, `minor`, or `major`. The workflow derives the
     next tag from the published stable releases.

3. Wait for the workflow to validate the tag, regenerate
   `docs/source/changelog.md`, create a local version-and-changelog commit and
   annotated tag, build and check the source and wheel distributions, push only
   the tag, create the GitHub release with generated notes, and publish to PyPI.
   The PyPI project description combines the README and generated changelog.
   Prereleases are published to TestPyPI instead.

No personal access token is needed. The workflow never pushes `main`.

## Rare recovery after a tag push

If the release job succeeds but GitHub release creation or package publishing
fails, rerun only the failed `github-release` or `publish` job. Each reuses the
distributions stored by the successful release job.

If a failed downstream job cannot be rerun, do not rerun the release job: it
correctly rejects existing tags. Do not force-move the tag.

1. Inspect the existing tag, package version, and documentation changelog:

   ```sh
   git fetch --tags origin
   git show --no-patch --decorate <tag>
   git show <tag>:aiopnsense/const.py
   git show <tag>:docs/source/changelog.md
   ```

2. If the tag and version are correct, build and check the distributions from
   the tag in a clean checkout:

   ```sh
   git switch --detach <tag>
   uv build
   uvx --from twine twine check dist/*
   ```

3. Inspect the GitHub release. If none exists, create it with the distributions;
   if a matching draft exists, finish that draft and attach them. Do not create
   a second release for the tag.

   ```sh
   gh release view <tag>
   gh release create <tag> dist/* --generate-notes --title <tag> --verify-tag
   # Or, for an existing matching draft:
   gh release upload <tag> dist/* --clobber
   gh release edit <tag> --draft=false
   ```

   Add `--prerelease` when the tag is a prerelease.

If the tag points to the wrong commit or contains the wrong version or
changelog, leave it unchanged and release a new, correct version instead.
