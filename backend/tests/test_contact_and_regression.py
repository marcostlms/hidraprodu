"""Iteration 4 tests: functional contact form + admin inbox + WhatsApp regression.

Covers new endpoints:
- POST /api/contact (public, validates email, required fields)
- GET  /api/contact (auth required)
- PATCH /api/contact/{id}/read (auth)
- DELETE /api/contact/{id} (auth)

Plus regression checks on /api/site/info whatsapp field and product listings.
"""

import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@hidrara.com.br"
ADMIN_PASSWORD = "Hidrara@2026"


@pytest.fixture(scope="session")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="session")
def admin_token(s):
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture()
def admin(admin_token):
    sess = requests.Session()
    sess.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {admin_token}",
    })
    return sess


# ---------- Contact submission (public) ----------
class TestContactSubmit:
    def test_submit_success(self, s):
        payload = {
            "name": "TEST User Iter4",
            "phone": "(16) 99999-1234",
            "email": "test_iter4@example.com",
            "message": "Olá, mensagem de teste da iteração 4.",
        }
        r = s.post(f"{API}/contact", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert isinstance(body.get("id"), str) and len(body["id"]) > 10

    def test_submit_invalid_email_422(self, s):
        payload = {
            "name": "TEST bad email",
            "phone": "",
            "email": "not-an-email",
            "message": "Mensagem qualquer aqui.",
        }
        r = s.post(f"{API}/contact", json=payload)
        assert r.status_code == 422, r.text

    def test_submit_missing_required_422(self, s):
        r = s.post(f"{API}/contact", json={})
        assert r.status_code == 422

    def test_submit_short_message_422(self, s):
        r = s.post(f"{API}/contact", json={
            "name": "TEST",
            "email": "x@y.com",
            "message": "hi",
        })
        assert r.status_code == 422


# ---------- Admin inbox (auth) ----------
class TestContactAdmin:
    def test_list_unauth_401(self, s):
        # Use fresh session with no auth cookies/headers
        raw = requests.Session()
        r = raw.get(f"{API}/contact")
        assert r.status_code == 401, f"expected 401 got {r.status_code}: {r.text}"

    def test_full_flow_list_read_delete(self, s, admin):
        # 1. create a fresh message
        payload = {
            "name": "TEST Flow Iter4",
            "phone": "16999990000",
            "email": "test_flow_iter4@example.com",
            "message": "TEST_ITER4_FLOW_MARKER mensagem para full flow.",
        }
        r = s.post(f"{API}/contact", json=payload)
        assert r.status_code == 200
        msg_id = r.json()["id"]

        # 2. list as admin, find created message
        r = admin.get(f"{API}/contact")
        assert r.status_code == 200
        msgs = r.json()
        assert isinstance(msgs, list) and len(msgs) >= 1
        found = next((m for m in msgs if m["id"] == msg_id), None)
        assert found is not None, "created message not returned in list"
        assert found["email"] == "test_flow_iter4@example.com"
        assert found["read"] is False
        assert found["name"] == "TEST Flow Iter4"
        assert "TEST_ITER4_FLOW_MARKER" in found["message"]

        # 3. mark as read
        r = admin.patch(f"{API}/contact/{msg_id}/read")
        assert r.status_code == 200
        # confirm
        msgs = admin.get(f"{API}/contact").json()
        after = next(m for m in msgs if m["id"] == msg_id)
        assert after["read"] is True

        # 4. delete
        r = admin.delete(f"{API}/contact/{msg_id}")
        assert r.status_code == 200
        msgs = admin.get(f"{API}/contact").json()
        assert not any(m["id"] == msg_id for m in msgs), "message should be gone"

    def test_cleanup_all_test_messages(self, admin):
        """Ensure inbox is not polluted with earlier TEST_ leftovers."""
        msgs = admin.get(f"{API}/contact").json()
        for m in msgs:
            if m.get("name", "").startswith("TEST ") or m.get("email", "").startswith("test_"):
                admin.delete(f"{API}/contact/{m['id']}")


# ---------- WhatsApp regression ----------
class TestWhatsAppUnified:
    def test_site_info_whatsapp_number(self, s):
        r = s.get(f"{API}/site/info")
        assert r.status_code == 200
        d = r.json()
        assert d["whatsapp"] == "551635081300", f"whatsapp is {d['whatsapp']}"
        # old wrong number must be gone
        raw = str(d)
        assert "5567" not in raw, "Old 5567 number still present in site/info"

    def test_units_all_unified_phone(self, s):
        d = s.get(f"{API}/site/info").json()
        for u in d["units"]:
            assert u["phone"] == "(16) 3508-1300"


# ---------- Products regression (short) ----------
class TestProductsRegression:
    def test_products_still_returns_24(self, s):
        r = s.get(f"{API}/products")
        assert r.status_code == 200
        assert len(r.json()) == 24

    def test_search_still_works(self, s):
        r = s.get(f"{API}/products", params={"q": "P954"})
        assert r.status_code == 200
        codes = {p["code"] for p in r.json()}
        assert {"P954208", "P954869"}.issubset(codes)
