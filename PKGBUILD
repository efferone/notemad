# Maintainer: efferone <efferone@proton.me>

pkgname=notemad
pkgver=0.1.0
pkgrel=1
pkgdesc="A tabbed note editor for Linux that never asks you to save"
arch=('any')
url="https://gitlab.com/efferone/notemad"
license=('GPL-3.0-or-later')
depends=('python' 'python-gobject' 'gtk4' 'libadwaita' 'gtksourceview5'
         'glib2' 'pango' 'hicolor-icon-theme')
makedepends=('python-build' 'python-installer' 'python-hatchling' 'python-wheel')
checkdepends=('python-pytest' 'python-hypothesis' 'xorg-server-xvfb')
# The release asset, not the forge-generated archive: those are rebuilt on
# demand, are not byte-stable, and use a notemad-v0.1.0/ prefix.
source=("$pkgname-$pkgver.tar.gz::https://gitlab.com/-/project/89045398/uploads/359cf36494af79bd6d211b411789b448/notemad-0.1.0.tar.gz")
sha256sums=('93a96cc292c8e79d5920bb654d3b4cf1ad38822f53c4254e018e8cb7b7d94cd0')

build() {
  cd "$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

check() {
  cd "$pkgname-$pkgver"
  # Parts of the suite build real widgets, which need a display even headless.
  xvfb-run -a python -m pytest
}

package() {
  cd "$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl

  install -Dm644 data/io.gitlab.efferone.Notemad.desktop \
    "$pkgdir/usr/share/applications/io.gitlab.efferone.Notemad.desktop"
  install -Dm644 \
    notemad/data/icons/hicolor/scalable/apps/io.gitlab.efferone.Notemad.svg \
    "$pkgdir/usr/share/icons/hicolor/scalable/apps/io.gitlab.efferone.Notemad.svg"
}
