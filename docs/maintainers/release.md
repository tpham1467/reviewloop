# Releasing reviewloop

One merge publishes everything. release-please keeps a release PR open from Conventional Commits; merging it
tags `vX.Y.Z` and `.github/workflows/release.yml` then publishes:

- the package to PyPI (Trusted Publishing, no token),
- `ghcr.io/tpham1467/reviewloop:<x.y.z>` and `:<major>` (multi-arch, SBOM, SLSA provenance, cosign keyless),
- the moving `v<major>` git tag that `uses: tpham1467/reviewloop@v0` resolves to,
- the GitLab CI/CD Catalog release, once the mirror below exists.

The version lives in `pyproject.toml`; release-please also bumps `__version__` in `src/reviewloop/__init__.py`, the
project's own entry in `uv.lock` (so `uv sync --locked` in the image build passes on the release commit) and the
annotated image version in `action.yml`, `templates/reviewloop.yml`, the Bitbucket examples and the `init` snippets. The manifest starts at `0.0.0`, so the first release is `0.1.0`
(`bump-minor-pre-major`). Releases created with `GITHUB_TOKEN` do not trigger other workflows, and neither does the
release PR: close and reopen it, or push an empty commit to its branch, to get CI to run on it.

## One-time setup (owner)

1. **PyPI pending publisher.** The distribution is named `reviewloop-ai` because `reviewloop` is taken on PyPI by
   another project. The import package and the command stay `reviewloop`. On pypi.org, Publishing, add a pending
   publisher: project name `reviewloop-ai`,
   owner `tpham1467`, repository `reviewloop`, workflow `release.yml`, environment `pypi`.
2. **GitHub environment.** Create the `pypi` environment in repository settings (optionally with a required
   reviewer).
3. **ghcr visibility.** After the first image push, set the package `reviewloop` to public under the profile's
   Packages settings and link it to the repository.
4. **Workflow permissions.** Settings, Actions, General: allow GitHub Actions to create and approve pull requests
   (release-please needs it).
5. **GitHub Marketplace (first listing, manual).** Needs the Marketplace developer agreement and 2FA, so it cannot
   be automated. Open the latest release on GitHub, tick "Publish this Action to the GitHub Marketplace", pick the
   categories and save. `action.yml` already carries the unique name, description, branding and author. Later
   releases update the listing from the tags.
6. **GitLab mirror and Catalog.** Create a gitlab.com project that mirrors this repository (push mirror or pull
   mirror), then Settings, General, Visibility: enable "CI/CD Catalog resource". Add a description to the project;
   the Catalog requires one. A tag `vX.Y.Z` arriving on the mirror runs the `release` job in `.gitlab-ci.yml`, whose
   `release:` keyword creates the release the Catalog lists, under a new tag `X.Y.Z` at the same commit (Catalog
   versions are semantic versions without the `v`). Users reference it as `reviewloop@X.Y.Z` or `reviewloop@0`. If the
   mirror is set to remove refs missing upstream, exclude these GitLab-only tags. Nothing in the GitHub workflow pushes to GitLab.

## Verify a release

```bash
cosign verify ghcr.io/tpham1467/reviewloop:0.1.0 \
  --certificate-identity-regexp '^https://github.com/tpham1467/reviewloop/\.github/workflows/release\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
