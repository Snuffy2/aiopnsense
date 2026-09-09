# Releasing aiopnsense

Release Please owns stable version selection, changelog updates, release tags,
and GitHub Releases. Publishing a GitHub Release starts a separate workflow that
builds and publishes the Python distributions.

## One-time repository setup

Configure a fine-grained personal access token as the
`RELEASE_PLEASE_TOKEN` Actions secret. Limit the token to this repository and
grant it these repository permissions:

- Contents: read and write
- Issues: read and write
- Pull requests: read and write

The separate credential allows pull requests and releases created by Release
Please to trigger the repository's normal workflows. Keep the release pull
request on the normal protected-branch path; do not grant it a branch-ruleset
bypass. If a tag ruleset blocks Release Please from creating `v*` tags, add the
token's actor only to that tag ruleset's bypass list.

Update the Trusted Publisher for `aiopnsense` on both PyPI and TestPyPI to use
the `publish-pypi.yml` workflow filename. Retain the `pypi` environment on PyPI
and the `testpypi` environment on TestPyPI. Remove the old `release.yml`
publisher registrations after the replacements are active.

## Stable releases

1. Merge release-ready changes into `main`.
2. Release Please creates or updates one release pull request. It updates
   `aiopnsense/const.py`, `.release-please-manifest.json`, and
   `docs/source/changelog.md`.
3. Review the generated version and changelog and let the normal required
   checks pass.
4. Merge the release pull request.
5. Release Please creates the matching `v`-prefixed tag and GitHub Release.
6. The **Publish Python Distribution** workflow builds and checks the source
   and wheel distributions, verifies that the tag, package version, and
   default-branch source agree, uploads them to the GitHub Release, and
   publishes them to PyPI through trusted publishing.

Release Please derives version bumps and changelog entries from Conventional
Commit subjects. Use `fix:` for a patch, `feat:` for a minor release, and a
breaking-change marker for a major release. With squash merging, the pull
request title becomes the relevant commit subject, so the **Lint PR title**
workflow accepts the same commit types configured for Release Please. The
generated changelog includes every accepted type and identifies each commit
author, so any accepted type can open a release pull request. Dependabot and
prek hook updates use the `deps:` type. To force an occasional version, include
a `Release-As: X.Y.Z` footer in the squash commit message.

## Prereleases

The Release Please configuration is intended for stable releases. A manually
published prerelease still starts **Publish Python Distribution**, which builds
from the published tag, uploads the distributions to that GitHub Release, and
publishes them to TestPyPI. Ensure `aiopnsense/const.py` already contains the
matching prerelease version before creating the tag.

## Failure handling

If Release Please cannot create or update its pull request, verify that the
`RELEASE_PLEASE_TOKEN` secret exists, has not expired, is authorized for this
repository, and has the documented contents, issues, and pull-request
permissions. Then rerun the failed workflow.

If release asset or package publication fails, rerun the failed workflow job.
The asset upload is idempotent and replaces matching files. Before any manual
recovery, inspect the published tag and release and build from that exact tag in
a clean checkout:

```sh
git switch --detach <tag>
uv build
uvx --from twine twine check dist/*
gh release upload <tag> dist/* --clobber
```
