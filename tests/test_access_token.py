import fis_app


def _get(client, path, addr, **kw):
    return client.get(path, environ_overrides={"REMOTE_ADDR": addr}, **kw)


def test_no_token_configured_means_no_restriction(monkeypatch):
    monkeypatch.delenv("SAILAB_ACCESS_TOKEN", raising=False)
    assert _get(fis_app.app.test_client(), "/", "192.168.18.50").status_code == 200


def test_remote_client_needs_token_but_local_never_does(monkeypatch):
    monkeypatch.setenv("SAILAB_ACCESS_TOKEN", "s3cret-token")
    c = fis_app.app.test_client()
    assert _get(c, "/", "192.168.18.50").status_code == 401
    assert _get(c, "/?token=wrong", "192.168.18.50").status_code == 401
    assert _get(c, "/", "127.0.0.1").status_code == 200
    assert _get(c, "/", "::1").status_code == 200


def test_token_via_query_sets_cookie_then_cookie_and_bearer_work(monkeypatch):
    monkeypatch.setenv("SAILAB_ACCESS_TOKEN", "s3cret-token")
    c = fis_app.app.test_client()
    r = _get(c, "/?token=s3cret-token", "10.0.0.7")
    assert r.status_code == 302 and "sailab_access=s3cret-token" in r.headers["Set-Cookie"]
    assert _get(c, "/", "10.0.0.7").status_code == 200
    assert _get(fis_app.app.test_client(), "/", "10.0.0.7", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
    assert _get(fis_app.app.test_client(), "/", "10.0.0.7", headers={"Authorization": "Bearer nope"}).status_code == 401
