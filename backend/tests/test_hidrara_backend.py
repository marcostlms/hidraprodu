"""Backend regression suite for Hidrara iteration 3.

Covers:
- /api/site/info (partners, mission/vision/values, 8 real units, unified phone)
- /api/products (24 products, categories distribution, real image_urls)
- /api/products search & category filters
- /api/auth login and admin flow
- /api/categories
"""

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://product-catalog-hub-26.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@hidrara.com.br"
ADMIN_PASSWORD = "Hidrara@2026"

EXPECTED_PARTNERS = ["Parker Hannifin", "Mann-Filter", "Donaldson Company, Inc."]

EXPECTED_UNITS = [
    ("Araraquara", "SP", "MATRIZ"),
    ("Sertãozinho", "SP", "FILIAL"),
    ("Dourados", "MS", "FILIAL"),
    ("Três Lagoas", "MS", "FILIAL"),
    ("Ribas do Rio Pardo", "MS", "FILIAL"),
    ("Teixeira de Freitas", "BA", "FILIAL"),
    ("Açailândia", "MA", "FILIAL"),
    ("Itajaí", "SC", "FILIAL"),
]

EXPECTED_CATEGORY_COUNTS = {
    "hidraulica": 7,
    "filtros": 5,
    "vedacoes": 5,
    "rolamentos": 3,
    "mangueiras-conexoes": 2,
    "pneumatica": 1,
    "chicotes-eletricos": 1,
}


@pytest.fixture(scope="session")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="session")
def auth(s):
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("role") == "admin"
    assert "access_token" in data
    return data["access_token"]


@pytest.fixture(scope="session")
def admin(s, auth):
    s.headers.update({"Authorization": f"Bearer {auth}"})
    return s


# ---------- Site info ----------
class TestSiteInfo:
    def test_status_and_shape(self, s):
        r = s.get(f"{API}/site/info")
        assert r.status_code == 200
        d = r.json()
        for k in ["partners", "units", "mission", "vision", "values", "phone_commercial"]:
            assert k in d, f"missing {k}"

    def test_partners_exact(self, s):
        d = s.get(f"{API}/site/info").json()
        assert d["partners"] == EXPECTED_PARTNERS

    def test_mission_vision_values_non_empty(self, s):
        d = s.get(f"{API}/site/info").json()
        assert isinstance(d["mission"], str) and len(d["mission"]) > 20
        assert isinstance(d["vision"], str) and len(d["vision"]) > 10
        assert isinstance(d["values"], str) and len(d["values"]) > 20

    def test_units_count_and_cities(self, s):
        d = s.get(f"{API}/site/info").json()
        assert len(d["units"]) == 8
        cities_states = [(u["city"], u["state"], u["role"]) for u in d["units"]]
        for expected in EXPECTED_UNITS:
            assert expected in cities_states, f"missing unit {expected}"

    def test_units_phone_unified(self, s):
        d = s.get(f"{API}/site/info").json()
        for u in d["units"]:
            assert u["phone"] == "(16) 3508-1300", f"{u['city']} phone: {u['phone']}"

    def test_matriz_role(self, s):
        d = s.get(f"{API}/site/info").json()
        matriz = [u for u in d["units"] if u["role"] == "MATRIZ"]
        assert len(matriz) == 1
        assert matriz[0]["city"] == "Araraquara"
        assert matriz[0]["state"] == "SP"


# ---------- Products ----------
class TestProducts:
    def test_count_is_24(self, s):
        r = s.get(f"{API}/products")
        assert r.status_code == 200
        assert len(r.json()) == 24

    def test_image_urls_valid(self, s):
        products = s.get(f"{API}/products").json()
        for p in products:
            iu = p.get("image_url") or ""
            assert iu.startswith("http") or iu.startswith("/hidrara/products/"), (
                f"bad image_url {p['code']}: {iu}"
            )

    def test_image_url_matches_code(self, s):
        products = s.get(f"{API}/products").json()
        # every product using local path must have code embedded
        for p in products:
            iu = p["image_url"]
            if iu.startswith("/hidrara/products/"):
                assert p["code"] in iu, f"code {p['code']} not in image_url {iu}"

    def test_specific_wk10002(self, s):
        products = s.get(f"{API}/products").json()
        wk = [p for p in products if p["code"] == "WK10002"]
        assert len(wk) == 1
        assert wk[0]["image_url"] == "/hidrara/products/WK10002.png"

    def test_category_distribution(self, s):
        products = s.get(f"{API}/products").json()
        counts = {}
        for p in products:
            counts[p["category"]] = counts.get(p["category"], 0) + 1
        assert counts == EXPECTED_CATEGORY_COUNTS, f"got {counts}"

    def test_search_p954(self, s):
        r = s.get(f"{API}/products", params={"q": "P954"})
        assert r.status_code == 200
        codes = [p["code"] for p in r.json()]
        assert "P954208" in codes
        assert "P954869" in codes

    def test_filter_by_filtros_category(self, s):
        r = s.get(f"{API}/products", params={"category": "filtros"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 5
        for p in data:
            assert p["category"] == "filtros"

    def test_search_by_name(self, s):
        r = s.get(f"{API}/products", params={"q": "mangueira"})
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_get_by_id(self, s):
        products = s.get(f"{API}/products").json()
        pid = products[0]["id"]
        r = s.get(f"{API}/products/{pid}")
        assert r.status_code == 200
        assert r.json()["id"] == pid


# ---------- Admin CRUD ----------
class TestAdminCRUD:
    def test_login(self, auth):
        assert isinstance(auth, str) and len(auth) > 20

    def test_me(self, admin):
        r = admin.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_create_update_delete_product(self, admin):
        payload = {
            "code": "TEST_ITER3_001",
            "name": "TEST Iteration 3 Product",
            "category": "hidraulica",
            "description": "temporary",
            "image_url": "/hidrara/products/WK10002.png",
            "brand": "TEST",
            "stock": "available",
            "featured": False,
        }
        # cleanup if exists
        r = admin.get(f"{API}/products", params={"q": "TEST_ITER3_001"})
        for p in r.json():
            admin.delete(f"{API}/products/{p['id']}")

        r = admin.post(f"{API}/products", json=payload)
        assert r.status_code == 200, r.text
        created = r.json()
        pid = created["id"]
        assert created["code"] == "TEST_ITER3_001"

        # verify persisted
        r2 = admin.get(f"{API}/products/{pid}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "TEST Iteration 3 Product"

        # update
        r3 = admin.put(f"{API}/products/{pid}", json={"name": "TEST Updated"})
        assert r3.status_code == 200
        assert r3.json()["name"] == "TEST Updated"

        # verify update persisted
        r4 = admin.get(f"{API}/products/{pid}")
        assert r4.json()["name"] == "TEST Updated"

        # delete
        r5 = admin.delete(f"{API}/products/{pid}")
        assert r5.status_code == 200

        # verify gone
        r6 = admin.get(f"{API}/products/{pid}")
        assert r6.status_code == 404


# ---------- Categories ----------
class TestCategories:
    def test_list(self, s):
        r = s.get(f"{API}/categories")
        assert r.status_code == 200
        slugs = [c["slug"] for c in r.json()]
        for expected in ["hidraulica", "filtros", "vedacoes", "rolamentos",
                         "mangueiras-conexoes", "pneumatica", "chicotes-eletricos"]:
            assert expected in slugs, f"missing category slug {expected}"
