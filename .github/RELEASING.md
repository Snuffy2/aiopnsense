# Releasing aiopnsense

## Stable releases

1. Merge the release-ready changes into the default branch.
2. Create and publish a GitHub Release targeted at that default branch, using an
   unused numeric, `v`-prefixed tag. The tag and target branch must initially
   resolve to the same commit. Publishing the release starts the **Release**
   workflow.
3. The workflow validates the tag and target, generates
   `docs/source/changelog.md`, creates a deterministic version-and-changelog
   commit, and builds and checks the source and wheel distributions.
4. The candidate is pushed to a temporary validation branch. The workflow
   dispatches these gates for that exact commit SHA and waits up to 30 minutes
   for their exact named jobs to pass:

   - `pytest check and post coverage`
   - `build-docs`
   - `Validate uv lock consistency`
   - `review`

5. After every gate passes, the workflow advances the default branch and
   annotated release tag together with leases, verifies their identity, uploads
   the distributions to the published GitHub Release, and publishes them to
   PyPI. The PyPI description includes the generated changelog.

The gates receive the candidate as an `expected_sha` input. The release
workflow verifies each workflow run ID, branch, SHA, GitHub Actions check suite,
and required job outcome before promotion. No personal access token is needed.

## Prereleases

Before publishing a prerelease, update `aiopnsense/const.py` to the intended
prerelease version and merge that change into the default branch. Then publish
a GitHub Release with the same explicit prerelease tag, targeted at the default
branch.

The workflow requires the source version, tag, and target to match, builds and
checks the distributions without changing the default branch or tag, uploads
them to the GitHub Release, and publishes them to TestPyPI.

## Failure handling and safe retries

A stable release stops before promotion when validation fails, a required check
does not complete before the timeout, or the default branch changes after the
candidate was selected. A failed stable run intentionally retains its temporary
validation branch for diagnosis. Before deleting one, verify its exact name and
candidate:

```sh
git fetch origin refs/heads/<temporary-ref>
git show -s --format='%H%n%s%n%P' FETCH_HEAD
git push origin --delete <temporary-ref>
```

The promotion uses an atomic push with leases. If it fails, inspect both remote
refs before retrying instead of assuming neither changed:

```sh
git fetch --tags origin
git log -1 --decorate origin/main
git show --no-patch --decorate <tag>
git show <tag>:aiopnsense/const.py
git show <tag>:docs/source/changelog.md
```

- If the default branch moved or the tag and branch do not identify the same
  release commit, stop and resolve that state before creating another release.
- If validation failed, fix the cause and publish a new release. Do not push the
  temporary candidate directly to bypass the required gates.
- If the branch and tag already identify the matching single-parent
  `Release <tag>` commit, rerunning the failed workflow resumes from that
  commit without creating another one.
- If only the package or asset publication failed, rerun the failed workflow
  job when GitHub permits it. Do not create a second release or force-move the
  tag manually.

For manual inspection or recovery, build from the existing tag in a clean
checkout and verify the distributions before uploading or publishing them:

```sh
git switch --detach <tag>
uv build
uvx --from twine twine check dist/*
gh release upload <tag> dist/* --clobber
```
