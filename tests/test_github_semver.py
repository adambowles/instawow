from __future__ import annotations

from datetime import datetime

import pytest

from instawow._sources.github import GithubResolver, _select_semver_releases, _semver_key
from instawow.definitions import Defn, Strategies, Strategy
from instawow.results import PkgFilesNotMatching
from ._fixtures.http import AddRoutes, Route

pytestmark = pytest.mark.usefixtures('_iw_config_ctx', '_iw_web_client_ctx')


@pytest.mark.parametrize('tag', [
    'v0.2.6', '0.2.7', 'v1.0.0', 'v10.20.30',
    'v1.2.3-alpha.1', '1.2.3+build.12', 'v1.2.3-rc.2+build.4',
])
def test_valid_semver_tags(tag: str):
    assert _semver_key(tag) is not None


@pytest.mark.parametrize('tag', [
    '', 'v1.2', '1.2.3.4', 'lib-v1.2026.10.1', 'forge-v0.1.1',
    'data-v2026.10.1', 'v1.02.3', 'v01.2.3', 'v1.2.3-01',
    'v1.2.3-alpha..1', 'v1.2.3foo', 'latest',
])
def test_invalid_semver_tags(tag: str):
    assert _semver_key(tag) is None


def make_release(tag: str, *, prerelease: bool = False, body: str | None = None):
    return {
        'tag_name': tag,
        'draft': False,
        'prerelease': prerelease,
        'body': body,
        'published_at': datetime(2026, 10, 25).isoformat() + '+00:00',
        'assets': [{'url': 'https://api.github.com/fake.zip', 'name': tag + '.zip'}],
    }


def test_semver_precedence_and_polestar_release_selection():
    releases = [
        make_release('lib-v1.2026.10.1'),
        make_release('forge-v0.1.1'),
        make_release('data-v2026.10.1'),
        make_release('v0.2.5'),
        make_release('v0.2.6'),
    ]
    assert [r['tag_name'] for r in _select_semver_releases(
        releases, include_prereleases=False
    )] == ['v0.2.6', 'v0.2.5']

    releases.append(make_release('v0.2.7'))
    assert _select_semver_releases(
        releases, include_prereleases=False
    )[0]['tag_name'] == 'v0.2.7'


def test_prerelease_semver_rules():
    releases = [
        make_release('v1.0.0-rc.10', prerelease=True),
        make_release('v1.0.0-rc.2', prerelease=True),
        make_release('v1.0.0'),
        make_release('v1.0.0-alpha', prerelease=True),
    ]
    assert [r['tag_name'] for r in _select_semver_releases(
        releases, include_prereleases=False
    )] == ['v1.0.0']
    assert [r['tag_name'] for r in _select_semver_releases(
        releases, include_prereleases=True
    )] == ['v1.0.0', 'v1.0.0-rc.10', 'v1.0.0-rc.2', 'v1.0.0-alpha']


@pytest.mark.parametrize(
    '_iw_mock_aiohttp_requests',
    [{r'//api\.github\.com/repos/SFX-WoW/Masque'}],
    indirect=True,
)
async def test_resolver_skips_polestar_auxiliary_releases(
    iw_add_routes: AddRoutes,
    monkeypatch: pytest.MonkeyPatch,
):
    from instawow._sources.github import GithubResolver

    releases = [
        make_release('lib-v1.2026.10.1'),
        make_release('data-v2026.10.1'),
        make_release('v0.2.5', body='Older'),
        make_release('v0.2.6', body=None),
        make_release('forge-v0.1.1'),
    ]
    iw_add_routes(Route(
        r'//api\.github\.com/repos/SFX-WoW/Masque/releases\?per_page=100',
        releases,
    ))
    resolver = GithubResolver()
    examined = []

    async def fake_find_match(release, desired_flavours):
        examined.append(release['tag_name'])
        return release, release['assets'][0]

    monkeypatch.setattr(resolver, '_GithubResolver__find_match', fake_find_match)
    result = await resolver.resolve_one(Defn(
        'github', 'SFX-WoW/Masque',
        strategies=Strategies({Strategy.SemverOnly: True}),
    ), None)
    assert examined == ['v0.2.6']
    assert result['version'] == 'v0.2.6'
    assert result['changelog_url'] == 'data:,'


@pytest.mark.parametrize(
    '_iw_mock_aiohttp_requests',
    [{r'//api\.github\.com/repos/SFX-WoW/Masque'}],
    indirect=True,
)
async def test_resolver_fails_closed_if_only_invalid_versions(
    iw_add_routes: AddRoutes,
    monkeypatch: pytest.MonkeyPatch,
):
    iw_add_routes(Route(
        r'//api\.github\.com/repos/SFX-WoW/Masque/releases\?per_page=100',
        [make_release('lib-v1.2026.10.1'), make_release('forge-v0.1.1')],
    ))
    resolver = GithubResolver()

    async def should_not_inspect_assets(release, desired_flavours):
        raise AssertionError('Unexpected asset lookup for invalid version')

    monkeypatch.setattr(resolver, '_GithubResolver__find_match', should_not_inspect_assets)
    with pytest.raises(PkgFilesNotMatching):
        await resolver.resolve_one(Defn(
            'github', 'SFX-WoW/Masque',
            strategies=Strategies({Strategy.SemverOnly: True}),
        ), None)



def test_semver_strategy_uri_roundtrip():
    defn = Defn.from_uri(
        'github:fitzforge/polestar#semver_only',
        known_sources={'github'},
        retain_unknown_source=False,
    )
    assert defn.strategies[Strategy.SemverOnly] is True
    assert defn.strategies[Strategy.VersionEq] is None
    assert defn.as_uri(include_strategies=True) == 'github:fitzforge/polestar#semver_only'


@pytest.mark.parametrize(
    '_iw_mock_aiohttp_requests',
    [{r'//api\.github\.com/repos/SFX-WoW/Masque'}],
    indirect=True,
)
async def test_nonsemver_addons_retain_upstream_resolution(
    iw_add_routes: AddRoutes,
    monkeypatch: pytest.MonkeyPatch,
):
    # Typical release tags observed on three different WoW addon projects.
    releases = [
        make_release('Pawn-2.14.0'),
        make_release('spoken/v3.5.1'),
        make_release('11.0.0-beta25', prerelease=True),
        make_release('10.1.0'),
    ]
    iw_add_routes(Route(
        r'//api\.github\.com/repos/SFX-WoW/Masque/releases\?per_page=100',
        releases,
    ))
    resolver = GithubResolver()
    seen = []

    async def fake_find_match(release, desired_flavours):
        seen.append(release['tag_name'])
        return release, release['assets'][0]

    monkeypatch.setattr(resolver, '_GithubResolver__find_match', fake_find_match)
    result = await resolver.resolve_one(Defn('github', 'SFX-WoW/Masque'), None)
    assert result['version'] == 'Pawn-2.14.0'
    assert seen == ['Pawn-2.14.0']


@pytest.mark.parametrize(
    '_iw_mock_aiohttp_requests',
    [{r'//api\.github\.com/repos/SFX-WoW/Masque'}],
    indirect=True,
)
async def test_prereleases_with_no_stable_remain_available_by_default(
    iw_add_routes: AddRoutes,
    monkeypatch: pytest.MonkeyPatch,
):
    iw_add_routes(Route(
        r'//api\.github\.com/repos/SFX-WoW/Masque/releases\?per_page=100',
        [make_release('spoken/v3.6.0-alpha.1', prerelease=True)],
    ))
    resolver = GithubResolver()

    async def fake_find_match(release, desired_flavours):
        return release, release['assets'][0]

    monkeypatch.setattr(resolver, '_GithubResolver__find_match', fake_find_match)
    result = await resolver.resolve_one(Defn('github', 'SFX-WoW/Masque'), None)
    assert result['version'] == 'spoken/v3.6.0-alpha.1'



def test_semver_strategy_persists_in_existing_sqlite_database(tmp_path):
    import sqlite3

    from instawow import pkg_db

    path = tmp_path / 'legacy.sqlite'
    legacy_schema = pkg_db._SCHEMA.replace(
        '    semver_only BOOLEAN NOT NULL DEFAULT 0,\n', ''
    )
    connection = sqlite3.connect(path)
    connection.executescript(legacy_schema)
    connection.close()

    with pkg_db.prepare_database(path) as upgraded:
        columns = {
            row['name'] for row in upgraded.execute('PRAGMA table_info(pkg_options)')
        }
        assert 'semver_only' in columns
        # A second opening must not attempt the ALTER TABLE again.
    with pkg_db.prepare_database(path) as reopened:
        assert reopened.execute('PRAGMA user_version').fetchone()[0] == 1
        reopened.execute(
            "INSERT INTO pkg (source, id, slug, name, description, url, download_url, "
            "date_published, version, changelog_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ('github', '123', 'test/repo', 'Test', '', '', '',
             '2026-10-25 00:00:00', 'v0.2.6', ''),
        )
        reopened.execute(
            "INSERT INTO pkg_options (any_flavour, any_release_type, version_eq, "
            "pkg_source, pkg_id) VALUES (0, 0, 1, 'github', '123')"
        )
        assert reopened.execute(
            "SELECT semver_only FROM pkg_options WHERE pkg_id = '123'"
        ).fetchone()[0] == 0
        reopened.execute(
            "UPDATE pkg_options SET semver_only = 1 WHERE pkg_id = '123'"
        )
        assert reopened.execute(
            "SELECT semver_only FROM pkg_options WHERE pkg_id = '123'"
        ).fetchone()[0] == 1
