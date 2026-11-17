```
1.  Version in pyproject.toml
2.  uv run ruff check . && uv run ruff format --check .
3.  uv run bandit -c pyproject.toml -r notemad
4.  uv run pytest
5.  uv run pip-audit
6.  gitleaks detect --source . --log-opts="--all --full-history"
7.  SECURITY.md
8.  git tag -a v0.1.0 -m "0.1.0" && git push origin main --tags
9.  git archive --format=tar.gz --prefix="notemad-0.1.0/" \
      -o notemad-0.1.0.tar.gz v0.1.0
10. Create the GitLab release, attach the tarball, register the asset link
11. updpkgsums against the published URL
12. namcap PKGBUILD
13. extra-x86_64-build
14. namcap notemad-0.1.0-1-any.pkg.tar.zst
15. Install the built package, run it, confirm the menu entry appears etc
16. makepkg --printsrcinfo > .SRCINFO
17. Commit and push to the AUR
```