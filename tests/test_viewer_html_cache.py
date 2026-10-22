import asyncio

from hololab.gateway.spa_staticfiles import SPAStaticFiles


def test_html_revalidates_without_disabling_asset_cache(tmp_path):
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "colmaputil").mkdir()
    (tmp_path / "colmaputil/index.html").write_text("<html>viewer</html>")
    (tmp_path / "asset.js").write_text("console.log('hashed asset')")
    app = SPAStaticFiles(directory=str(tmp_path), html=True)

    async def check():
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        for path in ["index.html", "colmaputil/index.html", "w/test", "."]:
            response = await app.get_response(path, scope)
            assert response.headers["cache-control"] == "no-cache"
            cached = await app.get_response(
                path,
                {
                    **scope,
                    "headers": [(b"if-none-match", response.headers["etag"].encode())],
                },
            )
            assert cached.status_code == 304
            assert cached.headers["cache-control"] == "no-cache"
        asset = await app.get_response("asset.js", scope)
        assert "cache-control" not in asset.headers

    asyncio.run(check())
