# SPDX-FileCopyrightText: 2026 Ram0 contributors
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "server" / "dashboard"


def test_login_is_only_the_authentication_surface():
    login = (DASHBOARD / "src/app/(auth)/login/login-form.tsx").read_text()
    assert "Sign in to Ram0" in login
    assert "<form" in login
    assert "testimonial" not in login.lower()
    assert "lg:flex" not in login
    assert not list((DASHBOARD / "public/images/logos").glob("*"))


def test_ram0_dashboard_uses_independent_svg_mark():
    mark = DASHBOARD / "public/images/ram0-mark.svg"
    assert mark.is_file()
    assert '<svg viewBox="0 0 96 96"' in mark.read_text()

    component = (DASHBOARD / "src/components/misc/theme-aware-logo.tsx").read_text()
    login = (DASHBOARD / "src/app/(auth)/login/login-form.tsx").read_text()
    assert "/images/ram0-mark.svg" in component
    assert "/images/ram0-mark.svg" in login
    assert "/images/logos/" not in component + login


def test_source_and_font_notices_are_present():
    assert (ROOT / "NOTICE").is_file()
    font_legal = DASHBOARD / "public/legal/fonts"
    required = {
        "README.md",
        "DM-Mono-OFL-1.1.txt",
        "Fustat-OFL-1.1.txt",
        "Inter-OFL-1.1.txt",
        "Roboto-Mono-OFL-1.1.txt",
    }
    assert required <= {path.name for path in font_legal.iterdir()}


def test_final_images_bundle_legal_material():
    api = (ROOT / "server/Dockerfile").read_text()
    dashboard = (DASHBOARD / "Dockerfile").read_text()
    required_destination = "/usr/share/licenses/ram0"
    assert required_destination in api
    assert "LICENSE" in api and "NOTICE" in api
    assert required_destination in dashboard
    assert "LICENSE" in dashboard and "NOTICE" in dashboard
    assert "legal/fonts" in dashboard


def test_readme_is_ram0_first_and_links_local_legal_files():
    readme = (ROOT / "README.md").read_text()
    prohibited = (
        "docs/images/banner-sm.png",
        "Y%20Combinator",
        "founders@mem0.ai",
    )
    assert not [text for text in prohibited if text in readme]
    assert "not affiliated with or endorsed by Mem0" in readme
    assert "](./LICENSE)" in readme
    assert "](./NOTICE)" in readme
